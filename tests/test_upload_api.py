"""Integration tests for the upload-related API.

Covers MERGE idempotency for POI creation, the beat traversal endpoint
GET /graph/poi/{name}/beats, force_create, coordinate validation and
name_variations on POI creation.
"""

from __future__ import annotations

import pytest

from src.connection import create_driver
from src.schema.constraints import apply_all
from src.seed.lenses import seed_lenses
from tests.conftest import _assert_test_port, needs_neo4j


@pytest.fixture(scope="module")
def clean_driver():
    """Create a driver with a clean DB + schema constraints + lenses.

    Overrides the conftest clean_driver to also seed lens data,
    which is required by beat traversal tests.
    """
    # Defense-in-depth: refuse the DETACH DELETE unless NEO4J_URI is the test
    # port (7688), independent of the suite-wide pytest_configure guard.
    _assert_test_port()
    d = create_driver()
    with d.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
    apply_all(d)
    seed_lenses(d)
    yield d
    d.close()


# ── MERGE idempotency ──


@needs_neo4j
class TestMergeIdempotency:
    """POST same POI twice → second call returns same ID, no duplicate."""

    def test_poi_merge_returns_same_id(self, client):
        payload = {
            "name": "Merge Test POI",
            "city_name": "paris",
            "latitude": 48.8530,
            "longitude": 2.3499,
            "short_description": "Test",
            "importance_tier": 1,
            "trigger_radius": 10,
            "typical_duration_min": 30,
            "kid_friendly": "yes",
        }
        resp1 = client.post("/api/v1/nodes/POI", json=payload)
        assert resp1.status_code == 201
        id1 = resp1.json()["id"]

        resp2 = client.post("/api/v1/nodes/POI", json=payload)
        assert resp2.status_code == 201
        id2 = resp2.json()["id"]

        assert id1 == id2, "MERGE should return the same node ID on duplicate POI name"

    def test_no_duplicate_poi_nodes(self, client):
        """After two POSTs with same name, only one POI node exists."""
        resp = client.get("/api/v1/nodes/POI?limit=200")
        items = resp.json()["items"]
        names = [i["properties"]["name"] for i in items]
        assert names.count("Merge Test POI") == 1

    def test_has_beat_edge_merge_idempotent(self, client):
        """POST same HAS_BEAT edge twice → same ID returned."""
        # Create a POI and a beat first
        poi_resp = client.post(
            "/api/v1/nodes/POI",
            json={
                "name": "Edge Merge POI",
                "city_name": "paris",
                "latitude": 48.85,
                "longitude": 2.35,
                "importance_tier": 1,
            },
        )
        poi_id = poi_resp.json()["id"]

        beat_resp = client.post(
            "/api/v1/nodes/NarrativeBeat",
            json={
                "script_body": "Edge merge test beat.",
            },
        )
        beat_id = beat_resp.json()["id"]

        edge_payload = {
            "source": {"label": "POI", "id": poi_id},
            "target": {"label": "NarrativeBeat", "id": beat_id},
        }
        e1 = client.post("/api/v1/edges/HAS_BEAT", json=edge_payload)
        assert e1.status_code == 201
        eid1 = e1.json()["id"]

        e2 = client.post("/api/v1/edges/HAS_BEAT", json=edge_payload)
        assert e2.status_code == 201
        eid2 = e2.json()["id"]

        assert eid1 == eid2, "MERGE should return the same edge ID on duplicate HAS_BEAT"


# ── Beat traversal endpoint ──


@needs_neo4j
class TestBeatTraversal:
    """GET /graph/poi/{name}/beats returns correct beat+lens data."""

    def test_returns_beats_for_seeded_poi(self, client):
        # Create POI
        client.post(
            "/api/v1/nodes/POI",
            json={
                "name": "Traversal Test POI",
                "city_name": "paris",
                "latitude": 48.85,
                "longitude": 2.35,
                "importance_tier": 1,
            },
        )

        # Create 2 beats with different lenses
        beat1 = client.post(
            "/api/v1/nodes/NarrativeBeat",
            json={
                "script_body": "Traversal beat one about history.",
                "version": 1,
                "active_status": "active",
                "duration_sec": 60,
            },
        ).json()

        beat2 = client.post(
            "/api/v1/nodes/NarrativeBeat",
            json={
                "script_body": "Traversal beat two about food.",
                "version": 1,
                "active_status": "active",
                "duration_sec": 180,
            },
        ).json()

        # Get POI ID
        poi_list = client.get("/api/v1/nodes/POI?limit=200").json()
        poi = next(p for p in poi_list["items"] if p["properties"]["name"] == "Traversal Test POI")

        # Get lens IDs
        lens_list = client.get("/api/v1/nodes/Lens?limit=50").json()
        hh_lens = next(l for l in lens_list["items"] if l["properties"]["name"] == "hidden_history")
        fc_lens = next(
            l for l in lens_list["items"] if l["properties"]["name"] == "historic_cuisine"
        )

        # Create HAS_BEAT edges
        client.post(
            "/api/v1/edges/HAS_BEAT",
            json={
                "source": {"label": "POI", "id": poi["id"]},
                "target": {"label": "NarrativeBeat", "id": beat1["id"]},
            },
        )
        client.post(
            "/api/v1/edges/HAS_BEAT",
            json={
                "source": {"label": "POI", "id": poi["id"]},
                "target": {"label": "NarrativeBeat", "id": beat2["id"]},
            },
        )

        # Create TAGGED_WITH edges
        client.post(
            "/api/v1/edges/TAGGED_WITH",
            json={
                "source": {"label": "NarrativeBeat", "id": beat1["id"]},
                "target": {"label": "Lens", "id": hh_lens["id"]},
            },
        )
        client.post(
            "/api/v1/edges/TAGGED_WITH",
            json={
                "source": {"label": "NarrativeBeat", "id": beat2["id"]},
                "target": {"label": "Lens", "id": fc_lens["id"]},
            },
        )

        # Now test the traversal endpoint
        resp = client.get("/api/v1/graph/poi/Traversal Test POI/beats?city_name=paris")
        assert resp.status_code == 200
        data = resp.json()
        assert data["poi_name"] == "Traversal Test POI"
        assert len(data["beats"]) == 2

        slugs = {b["lens_slug"] for b in data["beats"]}
        assert "hidden_history" in slugs
        assert "historic_cuisine" in slugs

    def test_returns_empty_for_unknown_poi(self, client):
        resp = client.get("/api/v1/graph/poi/NonExistentPOI/beats?city_name=paris")
        assert resp.status_code == 200
        assert resp.json()["beats"] == []

    def test_lensless_and_multilens_beats_each_appear_once(self, client):
        """RED-FIRST regression for the silent conflict-detection corruption.

        The old query used a mandatory ``-[:TAGGED_WITH]->(l:Lens)`` join, which
        (a) DROPPED lens-less active beats entirely — so review.html never saw
        them and a duplicate upload was not flagged as a conflict — and
        (b) DUPLICATED a multi-lens beat into N rows, inflating DB-beat counts
        and producing ambiguous hard-match rows.

        Assert both survive as exactly one row apiece, with a per-beat
        ``lens_slugs`` list carrying the full membership.
        """
        # POI
        client.post(
            "/api/v1/nodes/POI",
            json={
                "name": "Lens Shape POI",
                "city_name": "paris",
                "latitude": 48.859,
                "longitude": 2.339,
                "importance_tier": 1,
            },
        )
        poi_list = client.get("/api/v1/nodes/POI?limit=200").json()
        poi = next(p for p in poi_list["items"] if p["properties"]["name"] == "Lens Shape POI")

        # Beat A: NO lens tags (lens-less active beat)
        lensless = client.post(
            "/api/v1/nodes/NarrativeBeat",
            json={
                "script_body": "Lens-less active beat body.",
                "version": 1,
                "active_status": "active",
                "duration_sec": 60,
            },
        ).json()

        # Beat B: TWO lens tags (multi-lens beat)
        multilens = client.post(
            "/api/v1/nodes/NarrativeBeat",
            json={
                "script_body": "Multi-lens active beat body.",
                "version": 1,
                "active_status": "active",
                "duration_sec": 90,
            },
        ).json()

        for beat_id in (lensless["id"], multilens["id"]):
            client.post(
                "/api/v1/edges/HAS_BEAT",
                json={
                    "source": {"label": "POI", "id": poi["id"]},
                    "target": {"label": "NarrativeBeat", "id": beat_id},
                },
            )

        lens_list = client.get("/api/v1/nodes/Lens?limit=50").json()
        hh_lens = next(
            l for l in lens_list["items"] if l["properties"]["name"] == "hidden_history"
        )
        fc_lens = next(
            l for l in lens_list["items"] if l["properties"]["name"] == "historic_cuisine"
        )
        for lens in (hh_lens, fc_lens):
            client.post(
                "/api/v1/edges/TAGGED_WITH",
                json={
                    "source": {"label": "NarrativeBeat", "id": multilens["id"]},
                    "target": {"label": "Lens", "id": lens["id"]},
                },
            )

        resp = client.get("/api/v1/graph/poi/Lens Shape POI/beats?city_name=paris")
        assert resp.status_code == 200
        beats = resp.json()["beats"]

        # Each beat appears exactly once (no drops, no duplicate rows).
        ids = [b["id"] for b in beats]
        assert ids.count(lensless["id"]) == 1, "lens-less active beat must appear exactly once"
        assert ids.count(multilens["id"]) == 1, "multi-lens beat must appear exactly once"
        assert len(beats) == 2

        by_id = {b["id"]: b for b in beats}
        # Lens-less beat: empty lens list, scalar lens_slug is None.
        assert by_id[lensless["id"]]["lens_slugs"] == []
        assert by_id[lensless["id"]]["lens_slug"] is None
        # Multi-lens beat: both slugs present in the per-beat list.
        assert set(by_id[multilens["id"]]["lens_slugs"]) == {
            "hidden_history",
            "historic_cuisine",
        }


# ── POI location-first matching: force_create and coordinate validation ──


@needs_neo4j
class TestForceCreate:
    """force_create=true creates a new POI even when the name matches."""

    def test_force_create_makes_new_node(self, client):
        """Two POIs with same name but force_create=true → two distinct nodes."""
        base_payload = {
            "name": "Force Create Test POI",
            "city_name": "paris",
            "latitude": 48.8530,
            "longitude": 2.3499,
            "short_description": "Original",
            "importance_tier": 1,
            "trigger_radius": 10,
            "typical_duration_min": 30,
            "kid_friendly": "yes",
        }
        resp1 = client.post("/api/v1/nodes/POI", json=base_payload)
        assert resp1.status_code == 201
        id1 = resp1.json()["id"]

        force_payload = {
            **base_payload,
            "latitude": 48.8534,
            "longitude": 2.3502,
            "force_create": True,
        }
        resp2 = client.post("/api/v1/nodes/POI", json=force_payload)
        assert resp2.status_code == 201
        id2 = resp2.json()["id"]

        assert id1 != id2, "force_create=True should create a new node, not MERGE"

    def test_default_merge_still_works(self, client):
        """force_create=false (default) still MERGEs on name."""
        payload = {
            "name": "Default Merge POI",
            "city_name": "paris",
            "latitude": 48.85,
            "longitude": 2.35,
            "importance_tier": 1,
        }
        resp1 = client.post("/api/v1/nodes/POI", json=payload)
        id1 = resp1.json()["id"]
        resp2 = client.post("/api/v1/nodes/POI", json=payload)
        id2 = resp2.json()["id"]
        assert id1 == id2, "Default (no force_create) should MERGE on name"

    def test_force_create_results_in_two_nodes(self, client):
        """After force_create, querying POIs shows both nodes."""
        items = client.get("/api/v1/nodes/POI?limit=200").json()["items"]
        matching = [i for i in items if i["properties"]["name"] == "Force Create Test POI"]
        assert len(matching) == 2, "Should have two POIs with same name after force_create"


@needs_neo4j
class TestCoordinateValidation:
    """The backend rejects invalid coordinates."""

    def test_rejects_latitude_out_of_range(self, client):
        resp = client.post(
            "/api/v1/nodes/POI",
            json={
                "name": "Bad Lat POI",
                "city_name": "paris",
                "latitude": 999,
                "longitude": 2.35,
                "importance_tier": 1,
            },
        )
        assert resp.status_code == 422

    def test_rejects_longitude_out_of_range(self, client):
        resp = client.post(
            "/api/v1/nodes/POI",
            json={
                "name": "Bad Lng POI",
                "city_name": "paris",
                "latitude": 48.85,
                "longitude": -999,
                "importance_tier": 1,
            },
        )
        assert resp.status_code == 422

    def test_accepts_valid_coordinates(self, client):
        resp = client.post(
            "/api/v1/nodes/POI",
            json={
                "name": "Valid Coords POI",
                "city_name": "paris",
                "latitude": 48.8530,
                "longitude": 2.3499,
                "importance_tier": 1,
            },
        )
        assert resp.status_code == 201

    def test_rejects_latitude_missing_via_model(self, client):
        """POI without latitude should fail validation."""
        resp = client.post(
            "/api/v1/nodes/POI",
            json={
                "name": "No Lat POI",
                "city_name": "paris",
                "longitude": 2.35,
                "importance_tier": 1,
            },
        )
        assert resp.status_code == 422


# ── name_variations on POI creation ──


@needs_neo4j
class TestNameVariations:
    """POST /nodes/POI with name_variations field."""

    def test_poi_with_name_variations(self, client):
        payload = {
            "name": "Name Var Test POI",
            "city_name": "paris",
            "latitude": 48.861,
            "longitude": 2.341,
            "importance_tier": 1,
            "name_variations": ["Alt Name 1", "Alt Name 2"],
        }
        resp = client.post("/api/v1/nodes/POI", json=payload)
        assert resp.status_code == 201
        props = resp.json()["properties"]
        assert props["name_variations"] == ["Alt Name 1", "Alt Name 2"]

        # Confirm persistence via GET
        poi_list = client.get("/api/v1/nodes/POI?limit=200").json()
        match = next(p for p in poi_list["items"] if p["properties"]["name"] == "Name Var Test POI")
        assert match["properties"]["name_variations"] == ["Alt Name 1", "Alt Name 2"]

    def test_poi_without_name_variations(self, client):
        payload = {
            "name": "No Var Test POI",
            "city_name": "paris",
            "latitude": 48.862,
            "longitude": 2.342,
            "importance_tier": 1,
        }
        resp = client.post("/api/v1/nodes/POI", json=payload)
        assert resp.status_code == 201
        # Should not error — name_variations defaults to []

    def test_poi_with_invalid_name_variations(self, client):
        payload = {
            "name": "Invalid Var Test POI",
            "city_name": "paris",
            "latitude": 48.863,
            "longitude": 2.343,
            "importance_tier": 1,
            "name_variations": [123, "valid"],
        }
        resp = client.post("/api/v1/nodes/POI", json=payload)
        assert resp.status_code == 422, "Non-string items in name_variations should be rejected"


# ── Existing tests continued ──


@needs_neo4j
class TestBeatTraversalDeprecated:
    """Test deprecated beat exclusion."""

    def test_excludes_deprecated_beats(self, client):
        # Create a POI with a deprecated beat
        client.post(
            "/api/v1/nodes/POI",
            json={
                "name": "Deprecated Test POI",
                "city_name": "paris",
                "latitude": 48.87,
                "longitude": 2.37,
                "importance_tier": 1,
            },
        )

        beat = client.post(
            "/api/v1/nodes/NarrativeBeat",
            json={
                "script_body": "This beat is deprecated.",
                "version": 1,
                "active_status": "deprecated",
                "duration_sec": 60,
            },
        ).json()

        poi_list = client.get("/api/v1/nodes/POI?limit=200").json()
        poi = next(p for p in poi_list["items"] if p["properties"]["name"] == "Deprecated Test POI")

        lens_list = client.get("/api/v1/nodes/Lens?limit=50").json()
        lens = lens_list["items"][0]

        client.post(
            "/api/v1/edges/HAS_BEAT",
            json={
                "source": {"label": "POI", "id": poi["id"]},
                "target": {"label": "NarrativeBeat", "id": beat["id"]},
            },
        )
        client.post(
            "/api/v1/edges/TAGGED_WITH",
            json={
                "source": {"label": "NarrativeBeat", "id": beat["id"]},
                "target": {"label": "Lens", "id": lens["id"]},
            },
        )

        resp = client.get("/api/v1/graph/poi/Deprecated Test POI/beats?city_name=paris")
        assert resp.status_code == 200
        assert len(resp.json()["beats"]) == 0, "Deprecated beats should be excluded"
