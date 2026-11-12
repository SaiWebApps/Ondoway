"""A place's stored telling: written once per lens, checked, and split into pieces.

A recipe is one place told under one lens, for a solo walker, staged from outside the
door. It is planned, written and checked through the same seam a tour's stops use
(``plan_premium_authoring`` -> ``execute_premium_plan`` -> ``finalize_live_composition``),
so a stored telling passes exactly the gates a served stop passes. A place with a second
story also gets its full telling, planned and checked by the tour's own full-telling
seam (``plan_premium_full_telling`` -> ``finalize_premium_full_telling``). Nothing here calls a
provider, touches the graph or makes audio: the build job does those around it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from .contract import POI, BeatSequence, Sentence, TourInput
from .generation import GLUE_CLOSING, generate
from .premium_tour import (
    FullTelling,
    FullTellingBudget,
    PremiumTourPlan,
    SilentGlueClient,
    finalize_live_composition,
    full_telling_budget,
    full_telling_sequence,
    one_stop_source,
    plan_premium_authoring,
    plan_premium_full_telling,
    premium_authoring_policy_sha256,
)
from .routing import summarise_route
from .selection import CorpusSnapshot, build_poi_beat_plans_capped
from .verify import FaithfulnessChecker

#: Who the telling speaks to, and which side of the door it stages. Other walker types
#: and an inside version are separate recipes this module does not build yet.
RECIPE_REGISTER = "solo"
RECIPE_DOOR = "outside"
#: A recipe has no walk, so it has no routing engine version.
RECIPE_ROUTING_VERSION = "no-walk"
RECIPE_POLICY_VERSION = "ondoway-place-recipe-v2"
#: Fixed so the same place, lens and beats always plan a byte-identical request.
RECIPE_GENERATION_TIME = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
RECIPE_TOUR_MINUTES = 60
#: The writer's rule: the stop's point lands inside its first hundred words.
OPENING_WORDS = 100
#: The target length of each later piece, cut at sentence boundaries.
PIECE_WORDS = 80

PieceKind = Literal["opening", "body", "close", "full", "full_close"]
#: The pieces of the short telling, the one a stop plays on arrival. The full telling
#: is a piece of its own ("full"), and its close a second ("full_close").
SHORT_TELLING_KINDS: frozenset[str] = frozenset({"opening", "body", "close"})


@dataclass(frozen=True)
class PlaceRecipePlan:
    """One place under one lens, planned and ready to send to the writer."""

    recipe_id: str
    poi: POI
    lens: str | None
    plan: PremiumTourPlan
    input_sha256: str


@dataclass(frozen=True)
class RecipePiece:
    """One stretch of a stored telling, voiced as its own audio file."""

    order: int
    kind: PieceKind
    text: str
    beat_ids: tuple[str, ...]


def recipe_lenses(poi: POI, snapshot: CorpusSnapshot) -> tuple[str | None, ...]:
    """The lenses a place gets a recipe for: one with no lens, then each lens its
    beats carry. A lens no beat carries would tell the same story as no lens."""

    beats = snapshot.beats_by_poi.get(poi.id, ())
    return (None, *sorted({lens for beat in beats for lens in beat.lenses}))


def recipe_id(poi_id: str, lens: str | None) -> str:
    """The stored recipe's identity: what it tells, never how it was built, so a
    rebuild replaces its pieces instead of leaving a stale twin behind."""

    return f"{poi_id}:{lens or 'no-lens'}:{RECIPE_REGISTER}:{RECIPE_DOOR}"


def plan_place_recipe(
    poi: POI,
    snapshot: CorpusSnapshot,
    *,
    lens: str | None,
    city_slug: str,
    snapshot_sha256: str,
    voice_identity: str,
) -> PlaceRecipePlan:
    """Plan one place's telling under one lens, with no walk around it.

    The route is the place alone, its door priced as outside-only, so the writer is
    told to stage the exterior. The source is the place's seated beats and its close,
    never ``generate``'s opener or walking line, so nothing in it belongs to a tour.
    ``voice_identity`` joins the input hash because the stored audio depends on it.
    """

    tour_input = TourInput(
        start=(poi.lat, poi.lng),
        duration_min=RECIPE_TOUR_MINUTES,
        city_slug=city_slug,
        lenses=[lens] if lens else None,
        narration_register=RECIPE_REGISTER,
    )
    route = summarise_route(
        [poi],
        start_lat=poi.lat,
        start_lng=poi.lng,
        round_trip=False,
        duration_min=RECIPE_TOUR_MINUTES,
        spine_area=None,
        routing_client=None,
        visit_goes_inside={poi.id: False},
    )
    # A recipe is never the day's last stop, so its beats are capped as a middle
    # stop's are: the stops it is reused at seat the same beats.
    ((seated, overflow),) = build_poi_beat_plans_capped(
        route,
        snapshot,
        lenses=tour_input.lenses,
        end_is_none=tour_input.end is None,
        closes_a_day=False,
    )
    sequence = BeatSequence(
        poi_beats=(seated,), overflow_by_poi={poi.id: overflow} if overflow else {}
    )
    base = generate(
        sequence,
        route,
        tour_input,
        glue_client=SilentGlueClient(),
        now=RECIPE_GENERATION_TIME,
        validate_output=False,
    )
    source = one_stop_source(base, seated.beats, poi_name=poi.name, stop_index=0, n_stops=1)
    plan = plan_premium_authoring(
        source,
        sequence,
        route,
        snapshot=snapshot,
        snapshot_sha256=snapshot_sha256,
        routing_version=RECIPE_ROUTING_VERSION,
        policy_version=RECIPE_POLICY_VERSION,
        single_stop=0,
        standalone_place=True,
    )
    return PlaceRecipePlan(
        recipe_id=recipe_id(poi.id, lens),
        poi=poi,
        lens=lens,
        plan=plan,
        input_sha256=_input_sha256(plan, voice_identity, second_story=overflow),
    )


def _input_sha256(
    plan: PremiumTourPlan, voice_identity: str, *, second_story: tuple[str, ...]
) -> str:
    """Everything the stored tellings and their audio depend on: the exact writer
    request, the second story's beats, the writer's rules, and the voice."""

    beats = {beat.id: beat for beat in plan.snapshot.beats_by_poi.get(plan.route.pois[0].id, ())}
    payload = {
        "request_sha256": plan.units[0].request_sha256,
        "second_story": [beats[beat_id].model_dump(mode="json") for beat_id in second_story],
        "authoring_policy_sha256": premium_authoring_policy_sha256(),
        "recipe_policy": RECIPE_POLICY_VERSION,
        "voice": voice_identity,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def plan_place_full_telling(
    recipe: PlaceRecipePlan, sentences: tuple[Sentence, ...]
) -> tuple[PremiumTourPlan, FullTellingBudget] | None:
    """The place's full telling, told after its stored short telling: its second
    story (the beats the cap left over), sized from the short telling the way a tour
    sizes a major stop's. None when the place has no second story."""

    plan = recipe.plan
    if not plan.sequence.overflow_by_poi.get(recipe.poi.id):
        return None
    budget = full_telling_budget(sum(len(s.text.split()) for s in sentences))
    full_plan = plan_premium_full_telling(
        plan.source.model_copy(update={"script": sentences}),
        full_telling_sequence(plan.route, plan.snapshot, plan.sequence),
        plan.route,
        stop_index=0,
        budget=budget,
        snapshot=plan.snapshot,
        snapshot_sha256=plan.snapshot_sha256,
        routing_version=RECIPE_ROUTING_VERSION,
        policy_version=RECIPE_POLICY_VERSION,
        standalone_place=True,
    )
    return full_plan, budget


def full_telling_pieces(full: FullTelling, *, first_order: int) -> tuple[RecipePiece, ...]:
    """The full telling as two voiced pieces: the whole telling, played when the
    walker taps for more, and its close, played alone when they head on."""

    return (
        RecipePiece(
            order=first_order,
            kind="full",
            text=full.narration,
            beat_ids=_cited_beats(list(full.sentences)),
        ),
        RecipePiece(order=first_order + 1, kind="full_close", text=full.close_text, beat_ids=()),
    )


def finalize_place_recipe(
    recipe: PlaceRecipePlan,
    responses: tuple,
    *,
    faithfulness_checker: FaithfulnessChecker,
) -> tuple[Sentence, ...]:
    """The checked telling, through the gate set every served stop passes."""

    composition = finalize_live_composition(
        recipe.plan, responses, faithfulness_checker=faithfulness_checker
    )
    return composition.script.script


def split_pieces(sentences: tuple[Sentence, ...]) -> tuple[RecipePiece, ...]:
    """Cut a checked telling into pieces at sentence boundaries: the opening up to the
    first hundred words, later pieces of about eighty words, then the close alone.

    Pure, so the cut can change without rewriting anything: the stored sentences are
    the telling, and the pieces are only where the audio files start and stop.
    """

    closes = [s for s in sentences if s.source_id == GLUE_CLOSING]
    body = [s for s in sentences if s.source_id != GLUE_CLOSING]
    groups: list[tuple[PieceKind, list[Sentence]]] = []
    current: list[Sentence] = []
    words = 0
    for sentence in body:
        current.append(sentence)
        words += len(sentence.text.split())
        limit = OPENING_WORDS if not groups else PIECE_WORDS
        if words >= limit:
            groups.append(("opening" if not groups else "body", current))
            current, words = [], 0
    if current:
        groups.append(("opening" if not groups else "body", current))
    if closes:
        groups.append(("close", closes))
    return tuple(
        RecipePiece(
            order=order,
            kind=kind,
            text=" ".join(s.text for s in group),
            beat_ids=_cited_beats(group),
        )
        for order, (kind, group) in enumerate(groups)
    )


def _cited_beats(sentences: list[Sentence]) -> tuple[str, ...]:
    cited: list[str] = []
    for sentence in sentences:
        ids = (sentence.source_id, *sentence.also_cites) if sentence.source_type == "beat" else ()
        cited.extend(beat_id for beat_id in ids if beat_id not in cited)
    return tuple(cited)


__all__ = [
    "OPENING_WORDS",
    "PIECE_WORDS",
    "RECIPE_DOOR",
    "RECIPE_REGISTER",
    "SHORT_TELLING_KINDS",
    "PlaceRecipePlan",
    "RecipePiece",
    "finalize_place_recipe",
    "full_telling_pieces",
    "plan_place_full_telling",
    "plan_place_recipe",
    "recipe_id",
    "recipe_lenses",
    "split_pieces",
]
