"""Pydantic models for edge/relationship CRUD operations."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class RelType(StrEnum):
    """Valid relationship types from Schema_v3."""

    HAS_PROFILE = "HAS_PROFILE"
    IS_CAPTAIN_OF = "IS_CAPTAIN_OF"
    IS_CREW_OF = "IS_CREW_OF"
    MEMBER_OF = "MEMBER_OF"
    DAY_OF = "DAY_OF"
    PREFERS_LENS = "PREFERS_LENS"
    HAS_STOP = "HAS_STOP"
    ASSIGNED_TO = "ASSIGNED_TO"
    AT_POI = "AT_POI"
    PLAYS_BEAT = "PLAYS_BEAT"
    HAS_BEAT = "HAS_BEAT"
    TAGGED_WITH = "TAGGED_WITH"
    IS_PARENT_OF = "IS_PARENT_OF"
    WITHIN = "WITHIN"


class EdgeEndpoint(BaseModel):
    """Identifies a node by label + id."""

    label: str
    id: str


class EdgeCreate(BaseModel):
    """Request body for creating an edge."""

    source: EdgeEndpoint
    target: EdgeEndpoint
    properties: dict[str, Any] = {}


class EdgeUpdate(BaseModel):
    """Partial update — only provided fields are SET."""

    properties: dict[str, Any]


class EdgeResponse(BaseModel):
    """Returned from all edge endpoints."""

    id: str
    type: str
    source_id: str
    target_id: str
    properties: dict[str, Any]


class EdgeListResponse(BaseModel):
    """Paginated list of edges."""

    items: list[EdgeResponse]
    total: int
    skip: int
    limit: int
