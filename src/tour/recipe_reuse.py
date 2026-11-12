"""Serve a tour stop from its stored place recipe instead of a fresh writer call.

A stop takes its recipe only when the recipe tells exactly what the stop would be asked
to tell: the same beats, the same door, under the same lens, for a solo walker, written
and voiced under today's rules and voice, and at a stop that is not the day's last (a
recipe never closes a day). The stored telling then answers the stop's own request and
passes through the same finalizer and gates a written stop does, so a reused stop is
checked exactly like a fresh one. Every other stop goes to the writer.

A major stop's full telling is served the same way: the recipe's stored full telling
answers the stop's full-telling request and passes the full-telling finalizer, and the
writer is asked only when it does not.

The audio half is pure as well: it says which stretches of a stop's spoken text the
recipe's stored pieces already cover, so only the rest is voiced fresh.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .authoring import COMPOSE_MODEL
from .batch_regression_manifest import _canonical_bytes
from .certification_provider import PhysicalProviderResponse
from .contract import Sentence
from .generation import GLUE_CLOSING, GLUE_STAGING
from .place_recipes import (
    RECIPE_REGISTER,
    SHORT_TELLING_KINDS,
    plan_place_recipe,
    recipe_id,
)
from .premium_tour import PremiumComposeExecutor, PremiumComposeUnit, PremiumTourPlan


@dataclass(frozen=True)
class StoredRecipe:
    """A recipe as the graph holds it: what was built from, and the checked telling."""

    recipe_id: str
    input_sha256: str
    compose_model: str
    sentences: tuple[Sentence, ...]
    #: The place's full telling; empty when it has no second story or none passed.
    full_sentences: tuple[Sentence, ...] = ()


@dataclass(frozen=True)
class StoredPiece:
    """One voiced stretch of a stored telling."""

    kind: str
    text: str
    audio_url: str
    audio_duration_sec: float


@dataclass(frozen=True)
class RecipeUse:
    """One stop served from its recipe: the recipe, and the response that stands in
    for the writer's."""

    recipe: StoredRecipe
    response: PhysicalProviderResponse
    #: The beats the recipe's full telling was asked to tell: the place's second story.
    second_story: tuple[str, ...] = ()

    @property
    def recipe_id(self) -> str:
        return self.recipe.recipe_id


def wanted_recipe_ids(plan: PremiumTourPlan) -> dict[int, str]:
    """The recipe each stop could take, by stop index. Empty when the tour is not one
    a recipe is written for: another walker type, or more than one lens."""

    tour_input = plan.tour_input
    lenses = tour_input.lenses or []
    if tour_input.narration_register != RECIPE_REGISTER or len(lenses) > 1:
        return {}
    lens = lenses[0] if lenses else None
    last = len(plan.route.pois) - 1
    return {
        unit.stop_index: recipe_id(plan.route.pois[unit.stop_index].id, lens)
        for unit in plan.units
        if unit.stop_index != last
    }


def plan_recipe_reuse(
    plan: PremiumTourPlan,
    stored: Mapping[str, StoredRecipe],
    *,
    voice_identity: str,
) -> dict[int, RecipeUse]:
    """The stops this tour serves from a stored recipe, by stop index."""

    lenses = plan.tour_input.lenses or []
    lens = lenses[0] if lenses else None
    uses: dict[int, RecipeUse] = {}
    units = {unit.stop_index: unit for unit in plan.units}
    for stop_index, wanted in wanted_recipe_ids(plan).items():
        recipe = stored.get(wanted)
        if recipe is None or recipe.compose_model != COMPOSE_MODEL:
            continue
        unit = units[stop_index]
        poi = plan.route.pois[stop_index]
        fresh = plan_place_recipe(
            poi,
            plan.snapshot,
            lens=lens,
            city_slug=plan.tour_input.city_slug,
            snapshot_sha256=plan.snapshot_sha256,
            voice_identity=voice_identity,
        )
        asked = fresh.plan.units[0].authorized_request
        request = unit.authorized_request
        if (
            fresh.input_sha256 != recipe.input_sha256
            or set(asked.beats_by_id) != set(request.beats_by_id)
            or asked.door_state != request.door_state
        ):
            continue
        uses[stop_index] = RecipeUse(
            recipe=recipe,
            response=_stop_response(unit, recipe),
            second_story=tuple(fresh.plan.sequence.overflow_by_poi.get(poi.id, ())),
        )
    return uses


def full_telling_use(full_plan: PremiumTourPlan, use: RecipeUse) -> RecipeUse | None:
    """The stored full telling for a major stop whose short telling came from the same
    recipe, when the recipe's full telling was asked to tell exactly the second story
    this stop's is. Whether it tells it well enough is the full-telling finalizer's
    call, the same as for a written one."""

    told = use.recipe.full_sentences
    unit = full_plan.units[0]
    if not told or set(use.second_story) != set(unit.authorized_request.beats_by_id):
        return None
    placed = [sentence.model_copy(update={"stop_idx": unit.stop_index}) for sentence in told]
    return RecipeUse(recipe=use.recipe, response=_response(placed, use.recipe))


def _stop_response(unit: PremiumComposeUnit, recipe: StoredRecipe) -> PhysicalProviderResponse:
    """The stop's own walking and arrival lines as the stitch wrote them, then the
    recipe's telling and close, placed at this stop.

    A walking line includes the sights passed on the way in: they are beats, but not
    this place's beats, so the recipe does not tell them and they are kept. The
    stitch's staging line is left out: the recipe stages the place itself, and both
    read back to back repeat the same direction to look."""

    request = unit.authorized_request
    tour_lines = [
        sentence
        for sentence in request.stitched.script
        if (sentence.source_type == "beat" and sentence.source_id not in request.beats_by_id)
        or (
            sentence.source_type != "beat"
            and sentence.source_id not in (GLUE_CLOSING, GLUE_STAGING)
        )
    ]
    told = [
        sentence.model_copy(update={"stop_idx": unit.stop_index}) for sentence in recipe.sentences
    ]
    return _response([*tour_lines, *told], recipe)


def _response(sentences: list[Sentence], recipe: StoredRecipe) -> PhysicalProviderResponse:
    """Stored sentences in the shape the writer answers in, marked as the recipe's."""

    body = _canonical_bytes(
        {"sentences": [sentence.model_dump(mode="json") for sentence in sentences]}
    )
    return PhysicalProviderResponse(
        body=body,
        input_tokens=0,
        output_tokens=0,
        latency_ms=0,
        model=recipe.compose_model,
        provider_request_id=f"recipe:{recipe.recipe_id}",
        stop_reason="end_turn",
    )


class RecipeFirstExecutor:
    """Answers a stop from its recipe when it has one, else asks the writer.

    Each stored answer is served once: a stop that comes back for a second roll
    (the bounded retry after a refused check) goes to the writer."""

    def __init__(self, writer: PremiumComposeExecutor, uses: Mapping[int, RecipeUse]) -> None:
        self._writer = writer
        self._unserved = dict(uses)
        self.cost_bearing = writer.cost_bearing
        self.provider_name = writer.provider_name

    def execute(self, unit: PremiumComposeUnit) -> PhysicalProviderResponse:
        use = self._unserved.pop(unit.stop_index, None)
        return use.response if use is not None else self._writer.execute(unit)


def audio_parts(text: str, pieces: Sequence[StoredPiece]) -> list[str | StoredPiece] | None:
    """Split a stop's spoken text into stretches to voice fresh and the short telling's
    stored pieces to reuse, in playing order. The pieces must appear whole, in order
    and back to back; anything before them is voiced fresh. None when they do not, so
    the caller voices the whole text instead."""

    pieces = [piece for piece in pieces if piece.kind in SHORT_TELLING_KINDS]
    if not pieces:
        return None
    told = " ".join(piece.text for piece in pieces)
    if text == told:
        return list(pieces)
    if not text.endswith(" " + told):
        return None
    lead = text[: -len(told)].strip()
    return [lead, *pieces]


def stored_full_telling(text: str, pieces: Sequence[StoredPiece]) -> StoredPiece | None:
    """The recorded full telling, when it says exactly ``text``."""

    return next((p for p in pieces if p.kind == "full" and p.text == text), None)


__all__ = [
    "RecipeFirstExecutor",
    "RecipeUse",
    "StoredPiece",
    "StoredRecipe",
    "audio_parts",
    "full_telling_use",
    "plan_recipe_reuse",
    "stored_full_telling",
    "wanted_recipe_ids",
]
