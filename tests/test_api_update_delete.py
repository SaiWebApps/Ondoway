"""Integration tests for the node UPDATE + DELETE endpoints.

Also includes full lifecycle tests exercising all node CRUD
operations (create → read → update → read → delete → read).
Requires a running Neo4j instance.
"""

from __future__ import annotations

from tests.conftest import needs_neo4j


def _create_user(client, email: str) -> dict:
    """Helper: create a User and return the response JSON."""
    resp = client.post("/api/v1/nodes/User", json={"email": email})
    assert resp.status_code == 201
    return resp.json()


# ── Update node ──


@needs_neo4j
class TestUpdateNode:
    def test_update_email(self, client):
        user = _create_user(client, "update-me@test.com")
        resp = client.put(
            f"/api/v1/nodes/User/{user['id']}",
            json={"properties": {"email": "updated@test.com"}},
        )
        assert resp.status_code == 200
        assert resp.json()["properties"]["email"] == "updated@test.com"

    def test_update_preserves_other_fields(self, client):
        user = _create_user(client, "preserve@test.com")
        original_id = user["id"]
        original_created = user["properties"]["created_at"]

        client.put(
            f"/api/v1/nodes/User/{original_id}",
            json={"properties": {"email": "changed@test.com"}},
        )

        fetched = client.get(f"/api/v1/nodes/User/{original_id}").json()
        assert fetched["properties"]["created_at"] == original_created
        assert fetched["id"] == original_id

    def test_update_nonexistent_returns_404(self, client):
        resp = client.put(
            "/api/v1/nodes/User/does-not-exist",
            json={"properties": {"email": "x@x.com"}},
        )
        assert resp.status_code == 404

    def test_update_empty_properties_is_noop(self, client):
        user = _create_user(client, "noop@test.com")
        resp = client.put(
            f"/api/v1/nodes/User/{user['id']}",
            json={"properties": {}},
        )
        assert resp.status_code == 200
        assert resp.json()["properties"]["email"] == "noop@test.com"

    def test_update_poi_coordinates(self, client):
        poi = client.post(
            "/api/v1/nodes/POI",
            json={
                "name": "Movable Place",
                "city_name": "paris",
                "latitude": 40.0,
                "longitude": -74.0,
                "importance_tier": 1,
            },
        ).json()

        resp = client.put(
            f"/api/v1/nodes/POI/{poi['id']}",
            json={"properties": {"latitude": 41.0, "longitude": -73.0}},
        )
        assert resp.status_code == 200
        loc = resp.json()["properties"]["location"]
        assert abs(loc["lat"] - 41.0) < 0.001
        assert abs(loc["lng"] - (-73.0)) < 0.001


# ── Delete node ──


@needs_neo4j
class TestDeleteNode:
    def test_delete_returns_success(self, client):
        user = _create_user(client, "delete-me@test.com")
        resp = client.delete(f"/api/v1/nodes/User/{user['id']}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert resp.json()["id"] == user["id"]

    def test_deleted_node_is_gone(self, client):
        user = _create_user(client, "gone@test.com")
        client.delete(f"/api/v1/nodes/User/{user['id']}")
        resp = client.get(f"/api/v1/nodes/User/{user['id']}")
        assert resp.status_code == 404

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/v1/nodes/User/does-not-exist")
        assert resp.status_code == 404

    def test_delete_reduces_count(self, client):
        user = _create_user(client, "count-check@test.com")
        before = client.get("/api/v1/nodes/User").json()["total"]
        client.delete(f"/api/v1/nodes/User/{user['id']}")
        after = client.get("/api/v1/nodes/User").json()["total"]
        assert after == before - 1


# ── Full CRUD lifecycle ──


@needs_neo4j
class TestFullNodeLifecycle:
    def test_user_lifecycle(self, client):
        """Create → Read → Update → Read → Delete → Read (404)."""
        # Create
        created = _create_user(client, "lifecycle@test.com")
        node_id = created["id"]
        assert created["properties"]["email"] == "lifecycle@test.com"

        # Read
        fetched = client.get(f"/api/v1/nodes/User/{node_id}").json()
        assert fetched["id"] == node_id
        assert fetched["properties"]["email"] == "lifecycle@test.com"

        # Update
        updated = client.put(
            f"/api/v1/nodes/User/{node_id}",
            json={"properties": {"email": "lifecycle-v2@test.com"}},
        ).json()
        assert updated["properties"]["email"] == "lifecycle-v2@test.com"

        # Read again — confirm update persisted
        fetched2 = client.get(f"/api/v1/nodes/User/{node_id}").json()
        assert fetched2["properties"]["email"] == "lifecycle-v2@test.com"

        # Delete
        del_resp = client.delete(f"/api/v1/nodes/User/{node_id}")
        assert del_resp.json()["deleted"] is True

        # Read again — confirm gone
        assert client.get(f"/api/v1/nodes/User/{node_id}").status_code == 404

    def test_poi_lifecycle(self, client):
        """Create POI with geo → Update coords → Delete."""
        # Create
        created = client.post(
            "/api/v1/nodes/POI",
            json={
                "name": "Lifecycle POI",
                "city_name": "paris",
                "latitude": 48.8584,
                "longitude": 2.2945,
                "importance_tier": 1,
            },
        ).json()
        poi_id = created["id"]
        assert created["properties"]["name"] == "Lifecycle POI"
        assert abs(created["properties"]["location"]["lat"] - 48.8584) < 0.001

        # `name` is a protected merge-key property — updating it is
        # rejected (422), keeping the node's identity/merge key stable.
        rejected = client.put(
            f"/api/v1/nodes/POI/{poi_id}",
            json={"properties": {"name": "Renamed POI"}},
        )
        assert rejected.status_code == 422
        assert client.get(f"/api/v1/nodes/POI/{poi_id}").json()["properties"]["name"] == (
            "Lifecycle POI"
        )

        # Update a mutable property + coordinates (supplied as a pair).
        updated = client.put(
            f"/api/v1/nodes/POI/{poi_id}",
            json={
                "properties": {
                    "short_description": "A relocated lifecycle POI.",
                    "latitude": 51.5074,
                    "longitude": -0.1278,
                },
            },
        ).json()
        assert updated["properties"]["short_description"] == "A relocated lifecycle POI."
        assert abs(updated["properties"]["location"]["lat"] - 51.5074) < 0.001

        # Delete
        assert client.delete(f"/api/v1/nodes/POI/{poi_id}").json()["deleted"] is True
        assert client.get(f"/api/v1/nodes/POI/{poi_id}").status_code == 404

    def test_graph_endpoint_reflects_changes(self, client):
        """Graph endpoint should reflect created and deleted nodes."""
        before = len(client.get("/api/v1/graph").json()["nodes"])

        user = _create_user(client, "graph-check@test.com")
        during = len(client.get("/api/v1/graph").json()["nodes"])
        assert during == before + 1

        client.delete(f"/api/v1/nodes/User/{user['id']}")
        after = len(client.get("/api/v1/graph").json()["nodes"])
        assert after == before
