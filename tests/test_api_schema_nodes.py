"""Tests for node property schema introspection endpoints.

No Neo4j required — these endpoints read from Pydantic model definitions.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.models.nodes import NodeLabel


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture(scope="module")
def client():
    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    with TestClient(app) as c:
        yield c


# ── List all node schemas ──


class TestListNodeSchemas:
    def test_returns_200(self, client):
        resp = client.get("/api/v1/schema/nodes")
        assert resp.status_code == 200

    def test_returns_nine_types(self, client):
        data = client.get("/api/v1/schema/nodes").json()
        assert data["total"] == 9
        assert len(data["items"]) == 9

    def test_response_has_all_labels(self, client):
        data = client.get("/api/v1/schema/nodes").json()
        labels = {item["label"] for item in data["items"]}
        expected = {label.value for label in NodeLabel}
        assert labels == expected

    def test_each_item_has_properties_list(self, client):
        data = client.get("/api/v1/schema/nodes").json()
        for item in data["items"]:
            assert isinstance(item["properties"], list)
            assert len(item["properties"]) > 0

    def test_each_item_has_constraints_and_indexes(self, client):
        data = client.get("/api/v1/schema/nodes").json()
        for item in data["items"]:
            assert "constraints" in item
            assert "indexes" in item
            assert isinstance(item["constraints"], list)
            assert isinstance(item["indexes"], list)


# ── Get single node schema ──


class TestGetNodeSchema:
    def test_get_user_schema(self, client):
        data = client.get("/api/v1/schema/nodes/User").json()
        assert data["label"] == "User"
        prop_names = [p["name"] for p in data["properties"]]
        assert "email" in prop_names

    def test_user_email_is_required(self, client):
        data = client.get("/api/v1/schema/nodes/User").json()
        email_prop = next(p for p in data["properties"] if p["name"] == "email")
        assert email_prop["required"] is True
        assert email_prop["default"] is None

    def test_user_has_auto_id(self, client):
        data = client.get("/api/v1/schema/nodes/User").json()
        id_prop = next(p for p in data["properties"] if p["name"] == "id")
        assert id_prop["required"] is False
        assert id_prop["default"] == "(auto-generated UUID)"

    def test_user_has_created_at(self, client):
        data = client.get("/api/v1/schema/nodes/User").json()
        prop_names = [p["name"] for p in data["properties"]]
        assert "created_at" in prop_names

    def test_get_poi_schema(self, client):
        data = client.get("/api/v1/schema/nodes/POI").json()
        prop_names = [p["name"] for p in data["properties"]]
        assert "latitude" in prop_names
        assert "longitude" in prop_names
        assert "name" in prop_names

    def test_poi_has_defaults(self, client):
        data = client.get("/api/v1/schema/nodes/POI").json()
        kid_friendly = next(p for p in data["properties"] if p["name"] == "kid_friendly")
        assert kid_friendly["required"] is False
        assert kid_friendly["default"] == "yes"
        importance = next(p for p in data["properties"] if p["name"] == "importance_tier")
        assert importance["required"] is True
        assert importance["default"] is None

    def test_poi_has_point_index(self, client):
        data = client.get("/api/v1/schema/nodes/POI").json()
        assert "POINT:location" in data["indexes"]

    def test_user_has_unique_constraints(self, client):
        data = client.get("/api/v1/schema/nodes/User").json()
        assert "unique:id" in data["constraints"]
        assert "unique:email" in data["constraints"]

    def test_invalid_label_returns_422(self, client):
        resp = client.get("/api/v1/schema/nodes/FakeLabel")
        assert resp.status_code == 422


# ── Property shape ──


class TestPropertySchemaShape:
    def test_property_has_all_fields(self, client):
        data = client.get("/api/v1/schema/nodes/User").json()
        for prop in data["properties"]:
            assert "name" in prop
            assert "type" in prop
            assert "required" in prop
            assert "default" in prop

    def test_required_field_has_null_default(self, client):
        data = client.get("/api/v1/schema/nodes/User").json()
        email = next(p for p in data["properties"] if p["name"] == "email")
        assert email["required"] is True
        assert email["default"] is None

    def test_optional_field_has_non_null_default(self, client):
        data = client.get("/api/v1/schema/nodes/POI").json()
        kid_friendly = next(p for p in data["properties"] if p["name"] == "kid_friendly")
        assert kid_friendly["required"] is False
        assert kid_friendly["default"] is not None
