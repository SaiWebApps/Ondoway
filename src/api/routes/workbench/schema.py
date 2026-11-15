"""Schema introspection routes for node and relationship types."""

from __future__ import annotations

from fastapi import APIRouter

from src.api.crud import schema as schema_crud
from src.api.models.edges import RelType
from src.api.models.nodes import NodeLabel
from src.api.models.schema import (
    NodeTypeListResponse,
    NodeTypeSchema,
    RelTypeListResponse,
    RelTypeSchema,
)

router = APIRouter(tags=["schema"])


@router.get("/schema/nodes", response_model=NodeTypeListResponse)
def list_node_schemas():
    """List property schemas for all node types."""
    items = schema_crud.list_node_type_schemas()
    return NodeTypeListResponse(items=items, total=len(items))


@router.get("/schema/nodes/{label}", response_model=NodeTypeSchema)
def get_node_schema(label: NodeLabel):
    """Get property schema for a single node type."""
    return schema_crud.get_node_type_schema(label.value)


@router.get("/schema/relationships", response_model=RelTypeListResponse)
def list_rel_schemas():
    """List property schemas for all relationship types."""
    items = schema_crud.list_rel_type_schemas()
    return RelTypeListResponse(items=items, total=len(items))


@router.get("/schema/relationships/{rel_type}", response_model=RelTypeSchema)
def get_rel_schema(rel_type: RelType):
    """Get property schema for a single relationship type."""
    return schema_crud.get_rel_type_schema(rel_type.value)
