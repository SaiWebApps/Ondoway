"""CRUD routes for graph nodes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from neo4j import Session
from pydantic import ValidationError

from src.api.crud import nodes as crud
from src.api.dependencies import get_session
from src.api.models.nodes import (
    CREATE_MODELS,
    NodeLabel,
    NodeListResponse,
    NodeResponse,
    NodeUpdate,
)

router = APIRouter(tags=["nodes"])


@router.get("/nodes/{label}", response_model=NodeListResponse)
def list_nodes(
    label: NodeLabel,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
):
    """List all nodes of a given label with pagination."""
    items, total = crud.list_nodes(session, label.value, skip, limit)
    return NodeListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/nodes/{label}/{node_id}", response_model=NodeResponse)
def get_node(
    label: NodeLabel,
    node_id: str,
    session: Session = Depends(get_session),
):
    """Get a single node by label and ID."""
    node = crud.get_node(session, label.value, node_id)
    if node is None:
        raise HTTPException(404, f"{label.value} with id '{node_id}' not found")
    return node


@router.post("/nodes/{label}", response_model=NodeResponse, status_code=201)
def create_node(
    label: NodeLabel,
    body: dict,
    session: Session = Depends(get_session),
):
    """Create a new node of the given label."""
    model_cls = CREATE_MODELS[label]
    try:
        validated = model_cls(**body)
    except ValidationError as e:
        raise HTTPException(422, detail=str(e)) from None
    try:
        return crud.create_node(session, label.value, validated.model_dump())
    except Exception as e:
        if "Constraint" in type(e).__name__ or "ConstraintValidation" in str(e):
            raise HTTPException(409, f"Constraint violation: {e}") from None
        raise


@router.put("/nodes/{label}/{node_id}", response_model=NodeResponse)
def update_node(
    label: NodeLabel,
    node_id: str,
    body: NodeUpdate,
    session: Session = Depends(get_session),
):
    """Update a node's properties (partial update)."""
    result = crud.update_node(session, label.value, node_id, body.properties)
    if result is None:
        raise HTTPException(404, f"{label.value} with id '{node_id}' not found")
    return result


@router.delete("/nodes/{label}/{node_id}")
def delete_node(
    label: NodeLabel,
    node_id: str,
    session: Session = Depends(get_session),
):
    """Delete a node and all its relationships."""
    deleted = crud.delete_node(session, label.value, node_id)
    if not deleted:
        raise HTTPException(404, f"{label.value} with id '{node_id}' not found")
    return {"deleted": True, "id": node_id}
