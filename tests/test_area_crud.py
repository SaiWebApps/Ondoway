"""Integration tests for Area node CRUD and WITHIN edge support.

Tests POST /api/v1/nodes/Area and POST /api/v1/edges/WITHIN.
Requires a running Neo4j instance.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.schema.constraints import apply_all
from tests.conftest import needs_neo4j


@pytest.fixture(scope="module")
def clean_driver():
    """Create a driver with a clean DB + schema constraints."""
    from src.connection import create_driver
    from tests.conftest import _assert_test_port

    # Defense-in-depth: refuse the DETACH DELETE unless NEO4J_URI is the test
    # port (7688), independent of the suite-wide pytest_configure guard.
    _assert_test_port()
    d = create_driver()
    with d.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
    apply_all(d)
    yield d
    d.close()


@pytest.fixture(scope="module")
def client(clean_driver):
    """TestClient backed by a clean Neo4j database (no seed data)."""
    app = create_app()
    with TestClient(app) as c:
        yield c


VALID_AREA = {
    "name": "4th Arrondissement",
    "area_type": "district",
    "city_name": "Paris",
    "boundary": "POLYGON((2.34 48.85, 2.36 48.85, 2.36 48.86, 2.34 48.86, 2.34 48.85))",
    "centroid_lat": 48.855,
    "centroid_lng": 2.35,
    "short_description": "Historic heart of the Marais",
}


def _create_area(client, **overrides) -> dict:
    payload = {**VALID_AREA, **overrides}
    resp = client.post("/api/v1/nodes/Area", json=payload)
    assert resp.status_code == 201
    return resp.json()


def _create_poi(client, name: str, lat: float, lng: float) -> dict:
    resp = client.post(
        "/api/v1/nodes/POI",
        json={
            "name": name,
            "city_name": "paris",
            "latitude": lat,
            "longitude": lng,
            "importance_tier": 3,
        },
    )
    assert resp.status_code == 201
    return resp.json()


# ── Create Area ──


@needs_neo4j
class TestCreateArea:
    def test_returns_201(self, client):
        resp = client.post(
            "/api/v1/nodes/Area",
            json={
                **VALID_AREA,
                "name": "Test Create 201",
            },
        )
        assert resp.status_code == 201

    def test_response_has_generated_id(self, client):
        area = _create_area(client, name="ID Test Area")
        assert "id" in area
        assert len(area["id"]) > 10  # UUID

    def test_response_has_area_label(self, client):
        area = _create_area(client, name="Label Test Area")
        assert "Area" in area["labels"]

    def test_city_name_is_stored(self, client):
        area = _create_area(client, name="City Name Test")
        assert area["properties"]["city_name"] == "Paris"

    def test_area_type_is_stored(self, client):
        area = _create_area(client, name="Type Test Area")
        assert area["properties"]["area_type"] == "district"

    def test_centroid_is_geopoint(self, client):
        area = _create_area(client, name="Centroid Test")
        centroid = area["properties"]["centroid"]
        assert abs(centroid["lat"] - 48.855) < 0.001
        assert abs(centroid["lng"] - 2.35) < 0.001

    def test_boundary_is_stored(self, client):
        area = _create_area(client, name="Boundary Test")
        assert area["properties"]["boundary"].startswith("POLYGON((")

    def test_short_description_stored(self, client):
        area = _create_area(client, name="Desc Test")
        assert area["properties"]["short_description"] == "Historic heart of the Marais"

    def test_created_at_is_set(self, client):
        area = _create_area(client, name="Time Test Area")
        assert "created_at" in area["properties"]


# ── MERGE idempotency ──


@needs_neo4j
class TestAreaMerge:
    def test_same_compound_key_does_not_duplicate(self, client):
        name = "Merge Test Arr"
        _create_area(client, name=name)
        _create_area(client, name=name, short_description="updated")

        resp = client.get("/api/v1/nodes/Area")
        areas = [a for a in resp.json()["items"] if a["properties"]["name"] == name]
        assert len(areas) == 1

    def test_merge_updates_properties(self, client):
        name = "Merge Update Arr"
        _create_area(client, name=name, short_description="v1")
        area2 = _create_area(client, name=name, short_description="v2")
        assert area2["properties"]["short_description"] == "v2"

    def test_different_area_type_creates_separate_node(self, client):
        _create_area(client, name="Dual Type", area_type="district")
        _create_area(client, name="Dual Type", area_type="neighborhood")

        resp = client.get("/api/v1/nodes/Area")
        matches = [a for a in resp.json()["items"] if a["properties"]["name"] == "Dual Type"]
        assert len(matches) == 2

    def test_different_city_creates_separate_node(self, client):
        _create_area(client, name="Cross City", city_name="Paris")
        _create_area(client, name="Cross City", city_name="Boston")

        resp = client.get("/api/v1/nodes/Area")
        matches = [a for a in resp.json()["items"] if a["properties"]["name"] == "Cross City"]
        assert len(matches) == 2


# ── Validation ──


@needs_neo4j
class TestAreaValidation:
    def test_invalid_area_type_returns_422(self, client):
        resp = client.post(
            "/api/v1/nodes/Area",
            json={
                **VALID_AREA,
                "name": "Bad Type",
                "area_type": "bogus",
            },
        )
        assert resp.status_code == 422

    def test_missing_city_name_returns_422(self, client):
        payload = {**VALID_AREA, "name": "No City"}
        del payload["city_name"]
        resp = client.post("/api/v1/nodes/Area", json=payload)
        assert resp.status_code == 422

    def test_centroid_lat_out_of_range_returns_422(self, client):
        resp = client.post(
            "/api/v1/nodes/Area",
            json={
                **VALID_AREA,
                "name": "Bad Lat",
                "centroid_lat": 999,
            },
        )
        assert resp.status_code == 422

    def test_centroid_lng_out_of_range_returns_422(self, client):
        resp = client.post(
            "/api/v1/nodes/Area",
            json={
                **VALID_AREA,
                "name": "Bad Lng",
                "centroid_lng": 999,
            },
        )
        assert resp.status_code == 422

    def test_invalid_boundary_returns_422(self, client):
        resp = client.post(
            "/api/v1/nodes/Area",
            json={
                **VALID_AREA,
                "name": "Bad Boundary",
                "boundary": "not wkt",
            },
        )
        assert resp.status_code == 422


# ── WITHIN edge ──


@needs_neo4j
class TestWithinEdge:
    def test_poi_within_area_returns_201(self, client):
        area = _create_area(client, name="WITHIN Target Area")
        poi = _create_poi(client, "WITHIN Test POI", 48.855, 2.35)

        resp = client.post(
            "/api/v1/edges/WITHIN",
            json={
                "source": {"label": "POI", "id": poi["id"]},
                "target": {"label": "Area", "id": area["id"]},
            },
        )
        assert resp.status_code == 201
        assert resp.json()["type"] == "WITHIN"

    def test_area_within_area_returns_201(self, client):
        parent = _create_area(client, name="Parent City", area_type="city")
        child = _create_area(client, name="Child Arr", area_type="district")

        resp = client.post(
            "/api/v1/edges/WITHIN",
            json={
                "source": {"label": "Area", "id": child["id"]},
                "target": {"label": "Area", "id": parent["id"]},
            },
        )
        assert resp.status_code == 201

    def test_within_merge_no_duplicate(self, client):
        area = _create_area(client, name="WITHIN Merge Area", area_type="neighborhood")
        poi = _create_poi(client, "WITHIN Merge POI", 48.86, 2.36)

        body = {
            "source": {"label": "POI", "id": poi["id"]},
            "target": {"label": "Area", "id": area["id"]},
        }
        resp1 = client.post("/api/v1/edges/WITHIN", json=body)
        resp2 = client.post("/api/v1/edges/WITHIN", json=body)
        assert resp1.status_code == 201
        assert resp2.status_code == 201
        # Both should return the same edge (MERGE)
        assert resp1.json()["id"] == resp2.json()["id"]

    def test_invalid_source_label_returns_422(self, client):
        area = _create_area(client, name="WITHIN Invalid Src Area")
        user_resp = client.post("/api/v1/nodes/User", json={"email": "within@test.com"})
        user = user_resp.json()

        resp = client.post(
            "/api/v1/edges/WITHIN",
            json={
                "source": {"label": "User", "id": user["id"]},
                "target": {"label": "Area", "id": area["id"]},
            },
        )
        assert resp.status_code == 422
        assert "WITHIN source must be POI or Area" in resp.json()["detail"]

    def test_invalid_target_label_returns_422(self, client):
        poi = _create_poi(client, "WITHIN Bad Target POI", 48.85, 2.34)

        resp = client.post(
            "/api/v1/edges/WITHIN",
            json={
                "source": {"label": "POI", "id": poi["id"]},
                "target": {"label": "POI", "id": poi["id"]},
            },
        )
        assert resp.status_code == 422
        assert "WITHIN target must be Area" in resp.json()["detail"]


# ── Spatial query ──


@needs_neo4j
class TestAreaSpatialQuery:
    def test_centroid_is_real_geopoint(self, clean_driver, client):
        _create_area(
            client, name="Spatial Query Area", centroid_lat=48.853, centroid_lng=2.349
        )

        with clean_driver.session() as s:
            result = s.run(
                "MATCH (a:Area {name: 'Spatial Query Area'}) "
                "RETURN point.distance(a.centroid, point("
                "{latitude: 48.853, longitude: 2.349, srid: 4326}"
                ")) AS dist"
            ).single()
            assert result is not None
            assert result["dist"] < 1.0  # Within 1 meter — confirms it's a real GeoPoint
