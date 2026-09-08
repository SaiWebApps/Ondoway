"""Unit tests for edge/relationship API models — no Neo4j required."""

from src.api.models.edges import (
    EdgeCreate,
    EdgeEndpoint,
    EdgeListResponse,
    EdgeResponse,
    EdgeUpdate,
    RelType,
)
from src.schema.definitions import RELATIONSHIP_TYPES

# ── RelType enum ──


class TestRelType:
    def test_has_fourteen_types(self):
        assert len(RelType) == 14

    def test_all_expected_types_present(self):
        expected = set(RELATIONSHIP_TYPES)
        assert {rt.value for rt in RelType} == expected

    def test_types_are_string_values(self):
        for rt in RelType:
            assert isinstance(rt.value, str)


# ── EdgeResponse ──


class TestEdgeResponse:
    def test_can_construct_with_required_fields(self):
        resp = EdgeResponse(
            id="abc-123",
            type="HAS_PROFILE",
            source_id="user-1",
            target_id="profile-1",
            properties={"role": "primary"},
        )
        assert resp.id == "abc-123"
        assert resp.type == "HAS_PROFILE"
        assert resp.source_id == "user-1"
        assert resp.target_id == "profile-1"

    def test_properties_can_be_empty(self):
        resp = EdgeResponse(id="x", type="HAS_STOP", source_id="a", target_id="b", properties={})
        assert resp.properties == {}


# ── EdgeListResponse ──


class TestEdgeListResponse:
    def test_can_construct_empty_list(self):
        resp = EdgeListResponse(items=[], total=0, skip=0, limit=50)
        assert resp.items == []
        assert resp.total == 0

    def test_can_construct_with_items(self):
        item = EdgeResponse(id="1", type="HAS_PROFILE", source_id="a", target_id="b", properties={})
        resp = EdgeListResponse(items=[item], total=1, skip=0, limit=50)
        assert len(resp.items) == 1
        assert resp.items[0].id == "1"


# ── EdgeCreate ──


class TestEdgeCreate:
    def test_requires_source_and_target(self):
        body = EdgeCreate(
            source=EdgeEndpoint(label="User", id="u1"),
            target=EdgeEndpoint(label="Profile", id="p1"),
        )
        assert body.source.label == "User"
        assert body.target.id == "p1"

    def test_properties_default_to_empty_dict(self):
        body = EdgeCreate(
            source=EdgeEndpoint(label="User", id="u1"),
            target=EdgeEndpoint(label="Profile", id="p1"),
        )
        assert body.properties == {}


# ── EdgeUpdate ──


class TestEdgeUpdate:
    def test_requires_properties(self):
        body = EdgeUpdate(properties={"weight": 0.5})
        assert body.properties["weight"] == 0.5
