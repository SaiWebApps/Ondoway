"""CRUD routes for graph edges (relationships)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from neo4j import Session

from src.api.crud import edges as crud
from src.api.dependencies import get_session
from src.api.models.edges import (
    EdgeCreate,
    EdgeListResponse,
    EdgeResponse,
    EdgeUpdate,
    RelType,
)
from src.api.models.nodes import NodeLabel

router = APIRouter(tags=["edges"])


@router.get("/edges/{rel_type}", response_model=EdgeListResponse)
def list_edges(
    rel_type: RelType,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    source_id: str | None = Query(None),
    session: Session = Depends(get_session),
):
    """List all edges of a given type with pagination."""
    items, total = crud.list_edges(session, rel_type.value, skip, limit, source_id)
    return EdgeListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/edges/{rel_type}/{edge_id}", response_model=EdgeResponse)
def get_edge(
    rel_type: RelType,
    edge_id: str,
    session: Session = Depends(get_session),
):
    """Get a single edge by type and ID."""
    edge = crud.get_edge(session, rel_type.value, edge_id)
    if edge is None:
        raise HTTPException(404, f"{rel_type.value} with id '{edge_id}' not found")
    return edge


@router.post("/edges/{rel_type}", response_model=EdgeResponse, status_code=201)
def create_edge(
    rel_type: RelType,
    body: EdgeCreate,
    session: Session = Depends(get_session),
):
    """Create a new edge between two existing nodes."""
    try:
        NodeLabel(body.source.label)
    except ValueError:
        raise HTTPException(422, f"Invalid source label: '{body.source.label}'") from None
    try:
        NodeLabel(body.target.label)
    except ValueError:
        raise HTTPException(422, f"Invalid target label: '{body.target.label}'") from None

    # Prevent tagging beats with parent-only lenses
    if rel_type.value == "TAGGED_WITH" and body.target.label == "Lens":
        rec = session.run(
            "MATCH (l:Lens {id: $id}) RETURN l.is_parent AS is_parent, l.name AS name",
            id=body.target.id,
        ).single()
        if rec and rec["is_parent"]:
            raise HTTPException(
                422,
                f"Cannot tag a beat with parent-only lens '{rec['name']}'. "
                "Use a child or leaf lens.",
            )

    # Validate WITHIN edge source/target labels
    if rel_type.value == "WITHIN":
        if body.source.label not in ("POI", "Area"):
            raise HTTPException(422, "WITHIN source must be POI or Area")
        if body.target.label != "Area":
            raise HTTPException(422, "WITHIN target must be Area")

    result = crud.create_edge(
        session,
        rel_type.value,
        body.source.label,
        body.source.id,
        body.target.label,
        body.target.id,
        body.properties,
    )
    if result is None:
        raise HTTPException(
            404,
            f"Source {body.source.label} '{body.source.id}' or "
            f"target {body.target.label} '{body.target.id}' not found",
        )
    return result


@router.put("/edges/{rel_type}/{edge_id}", response_model=EdgeResponse)
def update_edge(
    rel_type: RelType,
    edge_id: str,
    body: EdgeUpdate,
    session: Session = Depends(get_session),
):
    """Update an edge's properties (partial update)."""
    result = crud.update_edge(session, rel_type.value, edge_id, body.properties)
    if result is None:
        raise HTTPException(404, f"{rel_type.value} with id '{edge_id}' not found")
    return result


@router.delete("/edges/{rel_type}/{edge_id}")
def delete_edge(
    rel_type: RelType,
    edge_id: str,
    session: Session = Depends(get_session),
):
    """Delete an edge."""
    deleted = crud.delete_edge(session, rel_type.value, edge_id)
    if not deleted:
        raise HTTPException(404, f"{rel_type.value} with id '{edge_id}' not found")
    return {"deleted": True, "id": edge_id}
