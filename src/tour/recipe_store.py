"""Place recipes in the graph: read whether one is current, and write one whole.

A ``PlaceRecipe`` node is one place told under one lens. It links to its place
(``FOR_PLACE``) and, in order, to its ``RecipePiece`` nodes (``HAS_PIECE``), each of
which links to the beats it tells (``CITES``) and holds its own audio file. A tour stop
served from a recipe links to it (``TOLD_FROM``), so the voicing pass reuses its pieces.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from neo4j import ManagedTransaction, Session

from .contract import Sentence
from .place_recipes import RECIPE_DOOR, RECIPE_REGISTER, PlaceRecipePlan, RecipePiece
from .premium_tour import PremiumTourPlan
from .recipe_reuse import (
    RecipeUse,
    StoredPiece,
    StoredRecipe,
    plan_recipe_reuse,
    wanted_recipe_ids,
)


@dataclass(frozen=True)
class VoicedPiece:
    """A recipe piece with the audio file made from its text."""

    piece: RecipePiece
    audio_url: str
    audio_duration_sec: float
    audio_provider: str


def current_input_sha256(session: Session, recipe_id: str) -> str | None:
    """The input hash of a stored recipe whose every piece has audio, else None.

    A recipe that was written but not fully voiced is not current: the build job
    rebuilds it rather than serve a place that cannot be played."""

    record = session.run(
        """
        MATCH (r:PlaceRecipe {id: $id})
        OPTIONAL MATCH (r)-[:HAS_PIECE]->(p:RecipePiece)
        RETURN r.input_sha256 AS sha, count(p) AS pieces, count(p.audio_url) AS voiced
        """,
        id=recipe_id,
    ).single()
    if record is None or record["pieces"] == 0 or record["pieces"] != record["voiced"]:
        return None
    return record["sha"]


def save_recipe(
    session: Session,
    recipe: PlaceRecipePlan,
    *,
    sentences: tuple[Sentence, ...],
    full_sentences: tuple[Sentence, ...],
    pieces: tuple[VoicedPiece, ...],
    built_at: str,
    commit_sha: str,
    compose_model: str,
) -> None:
    """Write one recipe whole, in one transaction: the recipe with its short and (when
    the place has one) full telling, its link to the place, and its pieces in order.
    Any pieces from an earlier build are removed first."""

    session.execute_write(
        _write_recipe,
        recipe_id=recipe.recipe_id,
        poi_id=recipe.poi.id,
        props={
            "id": recipe.recipe_id,
            "poi_id": recipe.poi.id,
            "lens": recipe.lens,
            "register": RECIPE_REGISTER,
            "door": RECIPE_DOOR,
            "input_sha256": recipe.input_sha256,
            "sentences_json": json.dumps(
                [s.model_dump(mode="json") for s in sentences], ensure_ascii=False
            ),
            "words": sum(len(s.text.split()) for s in sentences),
            "full_sentences_json": json.dumps(
                [s.model_dump(mode="json") for s in full_sentences], ensure_ascii=False
            ),
            "audio_duration_sec": round(sum(p.audio_duration_sec for p in pieces), 2),
            "built_at": built_at,
            "commit_sha": commit_sha,
            "compose_model": compose_model,
        },
        pieces=[
            {
                "id": f"{recipe.recipe_id}#{voiced.piece.order}",
                "order": voiced.piece.order,
                "kind": voiced.piece.kind,
                "text": voiced.piece.text,
                "beat_ids": list(voiced.piece.beat_ids),
                "audio_url": voiced.audio_url,
                "audio_duration_sec": voiced.audio_duration_sec,
                "audio_provider": voiced.audio_provider,
            }
            for voiced in pieces
        ],
    )


def _write_recipe(
    tx: ManagedTransaction, *, recipe_id: str, poi_id: str, props: dict, pieces: list[dict]
) -> None:
    tx.run(
        """
        MATCH (poi:POI {id: $poi_id})
        MERGE (r:PlaceRecipe {id: $recipe_id})
        SET r = $props
        MERGE (r)-[:FOR_PLACE]->(poi)
        WITH r
        OPTIONAL MATCH (r)-[:HAS_PIECE]->(old:RecipePiece)
        DETACH DELETE old
        """,
        recipe_id=recipe_id,
        poi_id=poi_id,
        props=props,
    )
    tx.run(
        """
        MATCH (r:PlaceRecipe {id: $recipe_id})
        UNWIND $pieces AS piece
        CREATE (p:RecipePiece)
        SET p = piece
        CREATE (r)-[:HAS_PIECE {order: piece.order}]->(p)
        WITH p, piece
        UNWIND piece.beat_ids AS beat_id
        MATCH (b:NarrativeBeat {id: beat_id})
        MERGE (p)-[:CITES]->(b)
        """,
        recipe_id=recipe_id,
        pieces=pieces,
    )


def load_recipes(session: Session, recipe_ids: list[str]) -> dict[str, StoredRecipe]:
    """The stored recipes among ``recipe_ids`` whose every piece has audio, by id."""

    rows = session.run(
        """
        MATCH (r:PlaceRecipe) WHERE r.id IN $ids
        OPTIONAL MATCH (r)-[:HAS_PIECE]->(p:RecipePiece)
        WITH r, count(p) AS pieces, count(p.audio_url) AS voiced
        WHERE pieces > 0 AND pieces = voiced
        RETURN r.id AS id, r.input_sha256 AS sha, r.compose_model AS model,
               r.sentences_json AS sentences, r.full_sentences_json AS full
        """,
        ids=recipe_ids,
    )
    return {
        row["id"]: StoredRecipe(
            recipe_id=row["id"],
            input_sha256=row["sha"],
            compose_model=row["model"],
            sentences=tuple(Sentence(**s) for s in json.loads(row["sentences"])),
            full_sentences=tuple(Sentence(**s) for s in json.loads(row["full"] or "[]")),
        )
        for row in rows
    }


def served_recipes(
    session: Session, plan: PremiumTourPlan, *, voice_identity: str | None
) -> dict[int, RecipeUse]:
    """The stops of a planned tour that its stored recipes serve, by stop index. None
    without a pinned voice, because a recipe's audio is reusable only in its own."""

    wanted = wanted_recipe_ids(plan)
    if voice_identity is None or not wanted:
        return {}
    stored = load_recipes(session, sorted(set(wanted.values())))
    return plan_recipe_reuse(plan, stored, voice_identity=voice_identity)


def link_stops_to_recipes(session: Session, recipe_by_item: dict[str, str]) -> None:
    """Record which recipe each served stop was told from."""

    if not recipe_by_item:
        return
    session.run(
        """
        UNWIND $links AS link
        MATCH (i:ItineraryItem {id: link.item}), (r:PlaceRecipe {id: link.recipe})
        MERGE (i)-[:TOLD_FROM]->(r)
        """,
        links=[{"item": item, "recipe": recipe} for item, recipe in recipe_by_item.items()],
    )


def stop_recipe_pieces(session: Session, item_id: str) -> tuple[StoredPiece, ...]:
    """The voiced pieces of the recipe a stop was told from, in order; empty if none."""

    rows = session.run(
        """
        MATCH (:ItineraryItem {id: $id})-[:TOLD_FROM]->(:PlaceRecipe)-[h:HAS_PIECE]->(p)
        RETURN p.kind AS kind, p.text AS text, p.audio_url AS url,
               p.audio_duration_sec AS seconds
        ORDER BY h.order
        """,
        id=item_id,
    )
    return tuple(
        StoredPiece(
            kind=row["kind"],
            text=row["text"],
            audio_url=row["url"],
            audio_duration_sec=row["seconds"],
        )
        for row in rows
    )


__all__ = [
    "VoicedPiece",
    "current_input_sha256",
    "link_stops_to_recipes",
    "load_recipes",
    "save_recipe",
    "served_recipes",
    "stop_recipe_pieces",
]
