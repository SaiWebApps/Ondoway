"""Integration tests for the node CREATE endpoint.

Tests POST /api/v1/nodes/{label} for creating nodes.
Requires a running Neo4j instance.
"""

from __future__ import annotations

from tests.conftest import needs_neo4j

# ── Create User ──


@needs_neo4j
class TestCreateUser:
    def test_returns_201(self, client):
        resp = client.post("/api/v1/nodes/User", json={"email": "new@test.com"})
        assert resp.status_code == 201

    def test_response_has_generated_id(self, client):
        resp = client.post("/api/v1/nodes/User", json={"email": "id-test@test.com"})
        data = resp.json()
        assert "id" in data
        assert len(data["id"]) > 10  # UUID

    def test_response_has_correct_label(self, client):
        resp = client.post("/api/v1/nodes/User", json={"email": "label@test.com"})
        assert "User" in resp.json()["labels"]

    def test_email_is_stored(self, client):
        resp = client.post("/api/v1/nodes/User", json={"email": "stored@test.com"})
        assert resp.json()["properties"]["email"] == "stored@test.com"

    def test_created_at_is_set(self, client):
        resp = client.post("/api/v1/nodes/User", json={"email": "time@test.com"})
        assert "created_at" in resp.json()["properties"]

    def test_node_is_retrievable_after_create(self, client):
        create_resp = client.post("/api/v1/nodes/User", json={"email": "fetch@test.com"})
        node_id = create_resp.json()["id"]
        get_resp = client.get(f"/api/v1/nodes/User/{node_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["properties"]["email"] == "fetch@test.com"


# ── Create POI (spatial point handling) ──


@needs_neo4j
class TestCreatePOI:
    def test_create_poi_with_lat_lng(self, client):
        resp = client.post(
            "/api/v1/nodes/POI",
            json={
                "name": "Test Place",
                "city_name": "paris",
                "latitude": 40.7128,
                "longitude": -74.0060,
                "importance_tier": 1,
            },
        )
        assert resp.status_code == 201
        props = resp.json()["properties"]
        assert props["name"] == "Test Place"

    def test_poi_location_is_serialized(self, client):
        resp = client.post(
            "/api/v1/nodes/POI",
            json={
                "name": "Geo Test",
                "city_name": "paris",
                "latitude": 51.5074,
                "longitude": -0.1278,
                "importance_tier": 1,
            },
        )
        loc = resp.json()["properties"]["location"]
        assert abs(loc["lat"] - 51.5074) < 0.001
        assert abs(loc["lng"] - (-0.1278)) < 0.001

    def test_poi_defaults_are_applied(self, client):
        resp = client.post(
            "/api/v1/nodes/POI",
            json={
                "name": "Default Test",
                "city_name": "paris",
                "latitude": 0.0,
                "longitude": 0.0,
                "importance_tier": 1,
            },
        )
        props = resp.json()["properties"]
        assert props["kid_friendly"] == "yes"
        assert props["importance_tier"] == 1

    def test_poi_city_name_normalized_to_canonical_lowercase(self, client):
        """Casing-fork guard (2026-07-03): POI.city_name is the canonical city
        slug the tour engine and the workbench city picker key off. It is
        normalized (stripped + lowercased) at ingestion so '  Paris ' stores
        'paris' and cannot fork the (name, city_name) MERGE key."""
        resp = client.post(
            "/api/v1/nodes/POI",
            json={
                "name": "Casing Guard Place",
                "city_name": "  Paris ",
                "latitude": 48.8584,
                "longitude": 2.2945,
                "importance_tier": 1,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["properties"]["city_name"] == "paris"

    def test_poi_city_name_casing_variants_merge_to_one_node(self, client):
        """Two uploads of the same POI name under different city_name casings
        MERGE to ONE node (no duplicate/fork) because both normalize to 'paris'
        — the exact silent-empty-beats failure the city picker exists to end."""
        base = {
            "name": "Merge Guard Place",
            "latitude": 48.8600,
            "longitude": 2.3400,
            "importance_tier": 1,
        }
        r1 = client.post("/api/v1/nodes/POI", json={**base, "city_name": "Paris"})
        r2 = client.post("/api/v1/nodes/POI", json={**base, "city_name": "paris"})
        assert r1.status_code == 201 and r2.status_code == 201
        assert r1.json()["id"] == r2.json()["id"], "casing variants must not fork the key"


# ── Create other node types ──


@needs_neo4j
class TestCreateOtherTypes:
    def test_create_profile(self, client):
        resp = client.post("/api/v1/nodes/Profile", json={"display_name": "Dad"})
        assert resp.status_code == 201
        assert resp.json()["properties"]["display_name"] == "Dad"

    def test_create_lens(self, client):
        resp = client.post(
            "/api/v1/nodes/Lens",
            json={
                "name": "test_lens",
                "display_label": "Test Lens",
            },
        )
        assert resp.status_code == 201

    def test_create_trip(self, client):
        resp = client.post(
            "/api/v1/nodes/Trip",
            json={
                "name": "Rome 2026",
                "start_date": "2026-06-01",
                "end_date": "2026-06-07",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["properties"]["status"] == "planning"

    def test_create_narrative_beat(self, client):
        resp = client.post(
            "/api/v1/nodes/NarrativeBeat",
            json={
                "script_body": "Once upon a time...",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["properties"]["version"] == 1


# ── Error cases ──


@needs_neo4j
class TestCreateErrors:
    def test_missing_required_field_returns_422(self, client):
        resp = client.post("/api/v1/nodes/User", json={})
        assert resp.status_code == 422

    def test_invalid_label_returns_422(self, client):
        resp = client.post("/api/v1/nodes/FakeLabel", json={"x": 1})
        assert resp.status_code == 422

    def test_node_count_increases(self, client):
        before = client.get("/api/v1/nodes/User").json()["total"]
        client.post("/api/v1/nodes/User", json={"email": "count@test.com"})
        after = client.get("/api/v1/nodes/User").json()["total"]
        assert after == before + 1
