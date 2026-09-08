"""Tests for relationship property schema introspection endpoints.

No Neo4j required — these endpoints read from schema definitions.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.schema.definitions import RELATIONSHIP_TYPES


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    with TestClient(app) as c:
        yield c


# ── List all relationship schemas ──


class TestListRelSchemas:
    def test_returns_200(self, client):
        resp = client.get("/api/v1/schema/relationships")
        assert resp.status_code == 200

    def test_returns_fourteen_types(self, client):
        data = client.get("/api/v1/schema/relationships").json()
        assert data["total"] == 14
        assert len(data["items"]) == 14

    def test_response_has_all_rel_types(self, client):
        data = client.get("/api/v1/schema/relationships").json()
        types = {item["type"] for item in data["items"]}
        assert types == set(RELATIONSHIP_TYPES)

    def test_each_item_has_properties_list(self, client):
        data = client.get("/api/v1/schema/relationships").json()
        for item in data["items"]:
            assert isinstance(item["properties"], list)
            assert len(item["properties"]) >= 2  # at least id + created_at

    def test_every_type_has_id_and_created_at(self, client):
        data = client.get("/api/v1/schema/relationships").json()
        for item in data["items"]:
            prop_names = [p["name"] for p in item["properties"]]
            assert "id" in prop_names
            assert "created_at" in prop_names


# ── Get single relationship schema ──


class TestGetRelSchema:
    def test_get_has_profile_schema(self, client):
        data = client.get("/api/v1/schema/relationships/HAS_PROFILE").json()
        assert data["type"] == "HAS_PROFILE"

    def test_has_profile_has_no_custom_properties(self, client):
        data = client.get("/api/v1/schema/relationships/HAS_PROFILE").json()
        # Only system fields: id, created_at
        assert len(data["properties"]) == 2

    def test_get_prefers_lens_has_weight(self, client):
        data = client.get("/api/v1/schema/relationships/PREFERS_LENS").json()
        prop_names = [p["name"] for p in data["properties"]]
        assert "weight" in prop_names

    def test_prefers_lens_weight_is_optional(self, client):
        data = client.get("/api/v1/schema/relationships/PREFERS_LENS").json()
        weight = next(p for p in data["properties"] if p["name"] == "weight")
        assert weight["required"] is False
        assert weight["default"] == 1.0
        assert weight["type"] == "float"

    def test_tagged_with_has_confidence(self, client):
        data = client.get("/api/v1/schema/relationships/TAGGED_WITH").json()
        prop_names = [p["name"] for p in data["properties"]]
        assert "confidence" in prop_names
        confidence = next(p for p in data["properties"] if p["name"] == "confidence")
        assert confidence["default"] == 1.0

    def test_has_beat_has_sort_order(self, client):
        data = client.get("/api/v1/schema/relationships/HAS_BEAT").json()
        prop_names = [p["name"] for p in data["properties"]]
        assert "sort_order" in prop_names
        sort_order = next(p for p in data["properties"] if p["name"] == "sort_order")
        assert sort_order["type"] == "int"
        assert sort_order["default"] == 0

    def test_invalid_rel_type_returns_422(self, client):
        resp = client.get("/api/v1/schema/relationships/FAKE_REL")
        assert resp.status_code == 422
