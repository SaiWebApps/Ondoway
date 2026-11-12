"""The build job: write, check, voice and store every place recipe a city lacks.

For each place and each of its lenses, the job plans the recipe, skips it when the
graph already holds a current copy, and otherwise writes it through the tour writer,
checks it with the tour gates, voices each piece once and saves it. The writer, the
fact checker and the voice are passed in, so this module chooses no provider.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from neo4j import Session

from src.audio.provider import OpenAITTSProvider

from .authoring import COMPOSE_MODEL
from .compose_gate import ComposeVerificationError
from .contract import POI, Sentence
from .place_recipes import (
    PlaceRecipePlan,
    finalize_place_recipe,
    full_telling_pieces,
    plan_place_full_telling,
    plan_place_recipe,
    recipe_id,
    recipe_lenses,
    split_pieces,
)
from .premium_tour import (
    EphemeralReceiptSink,
    FullTelling,
    PremiumBuildIdentity,
    PremiumComposeExecutor,
    PremiumTourPlan,
    execute_premium_plan,
    finalize_premium_full_telling,
)
from .recipe_store import VoicedPiece, current_input_sha256, save_recipe
from .selection import CorpusSnapshot
from .verify import FaithfulnessChecker

#: What one writing becomes once checked: a short telling, or a full telling.
_Checked = TypeVar("_Checked")

#: The first writing plus one re-roll, the same bound a served stop gets.
WRITING_ATTEMPTS = 2

OutcomeStatus = Literal["current", "planned", "built", "refused"]


class VoicedAudio(Protocol):
    audio_url: str
    duration_sec: float
    provider: str


#: Voice one piece: (text, storage key, place name) -> the stored audio file.
VoiceFn = Callable[..., VoicedAudio]


@dataclass(frozen=True)
class RecipeOutcome:
    """What happened to one place under one lens, with the provider usage it cost."""

    recipe_id: str
    poi_name: str
    lens: str | None
    status: OutcomeStatus
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    pieces: int = 0
    audio_seconds: float = 0.0
    seated_beats: int = 0
    overflow_beats: int = 0
    full_telling: bool = False
    reason: str = ""


def places_to_build(snapshot: CorpusSnapshot) -> list[POI]:
    """Every place with at least one beat, the most important first."""

    return sorted(
        (poi for poi in snapshot.pois if snapshot.beats_by_poi.get(poi.id)),
        key=lambda poi: (-poi.tier, -len(snapshot.beats_by_poi[poi.id]), poi.name),
    )


def pinned_voice_identity() -> str | None:
    """The voice an unnamed audio request speaks in, as recipe pieces record it; None
    when no voice provider is pinned. A recipe's audio is reused only under it."""

    provider = os.getenv("TTS_PROVIDER")
    if not provider:
        return None
    voice = os.getenv("OPENAI_VOICE", OpenAITTSProvider.DEFAULT_VOICE)
    return f"{provider}:{OpenAITTSProvider.DEFAULT_MODEL}:{voice}"


def piece_audio_key(voice_identity: str, text: str) -> str:
    """Storage key for one piece's audio: the voice and the words, nothing else."""

    return hashlib.sha256(f"{voice_identity}\x00{text}".encode()).hexdigest()[:32]


def build_recipes(
    session: Session,
    snapshot: CorpusSnapshot,
    places: Iterable[POI],
    *,
    city_slug: str,
    snapshot_sha256: str,
    executor: PremiumComposeExecutor,
    faithfulness_checker: FaithfulnessChecker,
    voice: VoiceFn,
    voice_identity: str,
    build_identity: PremiumBuildIdentity,
    dry_run: bool,
    should_continue: Callable[[RecipeOutcome], bool],
    only_lens: tuple[str | None] | None = None,
) -> Iterator[RecipeOutcome]:
    """Yield one outcome per place and lens, in order. ``should_continue`` sees every
    outcome that spent money and stops the job when it returns False. ``only_lens``
    holds the one lens to build (``(None,)`` for no lens) when not every lens is wanted."""

    for poi in places:
        lenses = recipe_lenses(poi, snapshot)
        for lens in lenses if only_lens is None else [x for x in lenses if x in only_lens]:
            try:
                recipe = plan_place_recipe(
                    poi,
                    snapshot,
                    lens=lens,
                    city_slug=city_slug,
                    snapshot_sha256=snapshot_sha256,
                    voice_identity=voice_identity,
                )
            except ValueError as exc:
                yield RecipeOutcome(
                    recipe_id=recipe_id(poi.id, lens),
                    poi_name=poi.name,
                    lens=lens,
                    status="refused",
                    reason=f"could not plan: {exc}",
                )
                continue
            seated = len(recipe.plan.sequence.poi_beats[0].beats)
            overflow = len(recipe.plan.sequence.overflow_by_poi.get(poi.id, ()))
            if current_input_sha256(session, recipe.recipe_id) == recipe.input_sha256:
                yield RecipeOutcome(
                    recipe_id=recipe.recipe_id,
                    poi_name=poi.name,
                    lens=lens,
                    status="current",
                    seated_beats=seated,
                    overflow_beats=overflow,
                )
                continue
            if dry_run:
                yield RecipeOutcome(
                    recipe_id=recipe.recipe_id,
                    poi_name=poi.name,
                    lens=lens,
                    status="planned",
                    seated_beats=seated,
                    overflow_beats=overflow,
                )
                continue
            outcome = _build_one(
                session,
                recipe,
                executor=executor,
                faithfulness_checker=faithfulness_checker,
                voice=voice,
                voice_identity=voice_identity,
                build_identity=build_identity,
                seated_beats=seated,
                overflow_beats=overflow,
            )
            yield outcome
            if not should_continue(outcome):
                return


def _build_one(
    session: Session,
    recipe: PlaceRecipePlan,
    *,
    executor: PremiumComposeExecutor,
    faithfulness_checker: FaithfulnessChecker,
    voice: VoiceFn,
    voice_identity: str,
    build_identity: PremiumBuildIdentity,
    seated_beats: int,
    overflow_beats: int,
) -> RecipeOutcome:
    usage = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    sentences, attempts, reason = _write(
        recipe.plan,
        executor,
        usage,
        lambda responses: finalize_place_recipe(
            recipe, responses, faithfulness_checker=faithfulness_checker
        ),
    )
    common = {
        "recipe_id": recipe.recipe_id,
        "poi_name": recipe.poi.name,
        "lens": recipe.lens,
        "seated_beats": seated_beats,
        "overflow_beats": overflow_beats,
    }
    if sentences is None:
        return RecipeOutcome(
            status="refused", reason=reason, attempts=attempts, **common, **_spent(usage)
        )
    full = _write_full_telling(recipe, sentences, executor, faithfulness_checker, usage)
    pieces = split_pieces(sentences)
    if full is not None:
        pieces = (*pieces, *full_telling_pieces(full, first_order=len(pieces)))
    voiced = []
    for piece in pieces:
        audio = voice(
            piece.text,
            stop_key=piece_audio_key(voice_identity, piece.text),
            poi_name=recipe.poi.name,
        )
        voiced.append(
            VoicedPiece(
                piece=piece,
                audio_url=audio.audio_url,
                audio_duration_sec=audio.duration_sec,
                audio_provider=audio.provider,
            )
        )
    save_recipe(
        session,
        recipe,
        sentences=sentences,
        full_sentences=full.sentences if full is not None else (),
        pieces=tuple(voiced),
        built_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        commit_sha=build_identity.commit_sha,
        compose_model=COMPOSE_MODEL,
    )
    return RecipeOutcome(
        status="built",
        attempts=attempts,
        pieces=len(voiced),
        audio_seconds=round(sum(v.audio_duration_sec for v in voiced), 2),
        full_telling=full is not None,
        **common,
        **_spent(usage),
    )


def _write_full_telling(
    recipe: PlaceRecipePlan,
    sentences: tuple[Sentence, ...],
    executor: PremiumComposeExecutor,
    faithfulness_checker: FaithfulnessChecker,
    usage: dict[str, int],
) -> FullTelling | None:
    """The place's full telling, or None when it has no second story or the checks
    refuse both writings. A recipe without one is still whole: tours write it fresh."""

    planned = plan_place_full_telling(recipe, sentences)
    if planned is None:
        return None
    plan, budget = planned
    full, _attempts, _reason = _write(
        plan,
        executor,
        usage,
        lambda responses: finalize_premium_full_telling(
            plan, responses, budget=budget, faithfulness_checker=faithfulness_checker
        ),
    )
    return full


def _write(
    plan: PremiumTourPlan,
    executor: PremiumComposeExecutor,
    usage: dict[str, int],
    finalize: Callable[[tuple], _Checked | None],
) -> tuple[_Checked | None, int, str]:
    """Write one plan through the tour seam, with the one re-roll a served stop gets.
    Returns the checked result (None if both writings were refused), the attempts
    made, and why the last one failed. Every attempt's provider usage is added up."""

    reason = ""
    for attempt in range(1, WRITING_ATTEMPTS + 1):
        try:
            responses = execute_premium_plan(
                plan, executor=executor, receipt_sink=EphemeralReceiptSink()
            )
        except Exception as exc:  # provider weather: recorded, the job moves on
            reason = f"writer call failed: {type(exc).__name__}: {exc}"
            continue
        for response in responses:
            usage["input"] += response.input_tokens
            usage["output"] += response.output_tokens
            usage["cache_write"] += response.cache_creation_input_tokens
            usage["cache_read"] += response.cache_read_input_tokens
        try:
            result = finalize(responses)
        except (ValueError, ComposeVerificationError) as exc:
            reason = f"refused by the checks: {exc}"
            continue
        if result is not None:
            return result, attempt, ""
        reason = "refused by the checks"
    return None, WRITING_ATTEMPTS, reason


def _spent(usage: dict[str, int]) -> dict[str, int]:
    return {
        "input_tokens": usage["input"],
        "output_tokens": usage["output"],
        "cache_creation_input_tokens": usage["cache_write"],
        "cache_read_input_tokens": usage["cache_read"],
    }


__all__ = [
    "WRITING_ATTEMPTS",
    "RecipeOutcome",
    "build_recipes",
    "piece_audio_key",
    "pinned_voice_identity",
    "places_to_build",
]
