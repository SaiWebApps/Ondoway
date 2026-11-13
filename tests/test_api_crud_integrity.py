"""Regression tests for graph CRUD data-integrity defects (2026-07-06).

Each test class targets one confirmed defect in src/api/crud/{nodes,edges}.py.
All are red-first: they fail against the pre-fix code and pass after the fix.

Requires test Neo4j on port 7688; the owning Make target starts it.
Uses the module-scoped `client` fixture from conftest (clean DB, no seed).
"""

from __future__ import annotations

from tests.conftest import needs_neo4j


def _create_poi(client, name: str, city_name: str = "paris", **overrides) -> dict:
    """Helper: create a POI and return the response JSON."""
    body = {
        "name": name,
        "city_name": city_name,
        "latitude": 48.8566,
        "longitude": 2.3522,
        "importance_tier": 1,
    }
    body.update(overrides)
    resp = client.post("/api/v1/nodes/POI", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_beat(client, script_body: str, **overrides) -> dict:
    """Helper: create a NarrativeBeat and return the response JSON."""
    body = {"script_body": script_body}
    body.update(overrides)
    resp = client.post("/api/v1/nodes/NarrativeBeat", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_profile(client, display_name: str) -> dict:
    resp = client.post("/api/v1/nodes/Profile", json={"display_name": display_name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_lens(client, name: str, display_label: str) -> dict:
    resp = client.post(
        "/api/v1/nodes/Lens", json={"name": name, "display_label": display_label}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── update_node / update_edge never overwrite identity/merge-key props ──


@needs_neo4j
class TestDefect1ProtectedKeys:
    def test_update_node_rejects_id_change(self, client):
        """PUT {id: 'X'} must be rejected (422) — rewriting n.id orphans the
        node from every id-keyed query and edge."""
        user = client.post(
            "/api/v1/nodes/User", json={"email": "protect-id@test.com"}
        ).json()
        original_id = user["id"]
        resp = client.put(
            f"/api/v1/nodes/User/{original_id}",
            json={"properties": {"id": "hijacked-id"}},
        )
        assert resp.status_code == 422, resp.text
        # The node keeps its original id.
        fetched = client.get(f"/api/v1/nodes/User/{original_id}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == original_id

    def test_update_node_rejects_created_at_change(self, client):
        user = client.post(
            "/api/v1/nodes/User", json={"email": "protect-created@test.com"}
        ).json()
        resp = client.put(
            f"/api/v1/nodes/User/{user['id']}",
            json={"properties": {"created_at": "1999-01-01T00:00:00Z"}},
        )
        assert resp.status_code == 422, resp.text

    def test_update_poi_rejects_name_change(self, client):
        """POI MERGE key is (name_key, city_name); rewriting `name` forks it."""
        poi = _create_poi(client, "Protected Name POI")
        resp = client.put(
            f"/api/v1/nodes/POI/{poi['id']}",
            json={"properties": {"name": "Renamed POI"}},
        )
        assert resp.status_code == 422, resp.text
        fetched = client.get(f"/api/v1/nodes/POI/{poi['id']}").json()
        assert fetched["properties"]["name"] == "Protected Name POI"

    def test_update_poi_rejects_city_name_change(self, client):
        poi = _create_poi(client, "Protected City POI")
        resp = client.put(
            f"/api/v1/nodes/POI/{poi['id']}",
            json={"properties": {"city_name": "reims"}},
        )
        assert resp.status_code == 422, resp.text
        fetched = client.get(f"/api/v1/nodes/POI/{poi['id']}").json()
        assert fetched["properties"]["city_name"] == "paris"

    def test_update_poi_rejects_name_key_change(self, client):
        """The derived canonical key (the name canonicalization below) is protected
        too — editing it directly would fork just as rewriting `name` would."""
        poi = _create_poi(client, "Protected Key POI")
        resp = client.put(
            f"/api/v1/nodes/POI/{poi['id']}",
            json={"properties": {"name_key": "hijacked key"}},
        )
        assert resp.status_code == 422, resp.text

    def test_update_area_rejects_area_type_change(self, client):
        area = client.post(
            "/api/v1/nodes/Area",
            json={
                "name": "Le Marais",
                "area_type": "district",
                "city_name": "paris",
                "boundary": "POLYGON((2.35 48.85, 2.36 48.85, 2.36 48.86, 2.35 48.86, 2.35 48.85))",
                "centroid_lat": 48.8566,
                "centroid_lng": 2.3610,
            },
        ).json()
        resp = client.put(
            f"/api/v1/nodes/Area/{area['id']}",
            json={"properties": {"area_type": "neighborhood"}},
        )
        assert resp.status_code == 422, resp.text

    def test_update_narrativebeat_rejects_id_change(self, client):
        beat = _create_beat(client, "Beat whose id must not be editable.")
        resp = client.put(
            f"/api/v1/nodes/NarrativeBeat/{beat['id']}",
            json={"properties": {"id": "hijacked"}},
        )
        assert resp.status_code == 422, resp.text

    def test_update_node_allows_ordinary_property(self, client):
        """A non-protected key still updates normally (no over-blocking)."""
        user = client.post(
            "/api/v1/nodes/User", json={"email": "ordinary@test.com"}
        ).json()
        resp = client.put(
            f"/api/v1/nodes/User/{user['id']}",
            json={"properties": {"email": "ordinary-v2@test.com"}},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["properties"]["email"] == "ordinary-v2@test.com"

    def test_update_edge_rejects_id_change(self, client):
        user = client.post(
            "/api/v1/nodes/User", json={"email": "edge-protect@test.com"}
        ).json()
        profile = _create_profile(client, "Edge Protect Profile")
        edge = client.post(
            "/api/v1/edges/HAS_PROFILE",
            json={
                "source": {"label": "User", "id": user["id"]},
                "target": {"label": "Profile", "id": profile["id"]},
            },
        ).json()
        resp = client.put(
            f"/api/v1/edges/HAS_PROFILE/{edge['id']}",
            json={"properties": {"id": "hijacked-edge-id"}},
        )
        assert resp.status_code == 422, resp.text
        fetched = client.get(f"/api/v1/edges/HAS_PROFILE/{edge['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == edge["id"]

    def test_update_edge_allows_ordinary_property(self, client):
        user = client.post(
            "/api/v1/nodes/User", json={"email": "edge-ok@test.com"}
        ).json()
        profile = _create_profile(client, "Edge OK Profile")
        edge = client.post(
            "/api/v1/edges/HAS_PROFILE",
            json={
                "source": {"label": "User", "id": user["id"]},
                "target": {"label": "Profile", "id": profile["id"]},
            },
        ).json()
        resp = client.put(
            f"/api/v1/edges/HAS_PROFILE/{edge['id']}",
            json={"properties": {"weight": 0.9}},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["properties"]["weight"] == 0.9


# ── A POI never forks under name casing / whitespace ──


@needs_neo4j
class TestDefect3NameCanonicalization:
    def test_name_casing_and_whitespace_variants_merge_to_one_node(self, client):
        """'Notre-Dame', 'notre-dame', 'Notre-Dame ' (trailing space) for the
        same city must MERGE to ONE POI node, not fork into three."""
        r1 = _create_poi(client, "Notre-Dame")
        r2 = _create_poi(client, "notre-dame")
        r3 = _create_poi(client, "Notre-Dame ")  # trailing space
        assert r1["id"] == r2["id"] == r3["id"], (
            "name casing/whitespace variants must not fork the MERGE key"
        )
        # Exactly one Notre-Dame node exists.
        pois = client.get("/api/v1/nodes/POI?limit=200").json()["items"]
        matches = [p for p in pois if p["id"] == r1["id"]]
        assert len(matches) == 1

    def test_internal_whitespace_is_collapsed(self, client):
        """Collapsing internal runs of whitespace also dedups."""
        a = _create_poi(client, "Arc de  Triomphe")  # double space
        b = _create_poi(client, "Arc de Triomphe")  # single space
        assert a["id"] == b["id"]

    def test_display_name_casing_is_preserved(self, client):
        """MERGE on the derived key must NOT destroy display casing: n.name
        keeps the last-written display form."""
        poi = _create_poi(client, "Sacré-Cœur")
        fetched = client.get(f"/api/v1/nodes/POI/{poi['id']}").json()
        assert fetched["properties"]["name"] == "Sacré-Cœur"

    def test_different_names_still_distinct(self, client):
        """Canonicalization must not over-merge genuinely different names."""
        a = _create_poi(client, "Louvre")
        b = _create_poi(client, "Orsay")
        assert a["id"] != b["id"]


# ── The Beat MERGE key is never a GLOBAL script_body ──


@needs_neo4j
class TestDefect4BeatMergeScope:
    def test_identical_script_body_two_pois_two_distinct_beats(self, client):
        """Two beats with identical script_body attached to two different POIs
        must be two DISTINCT NarrativeBeat nodes — neither overwrites the other.
        """
        poi_a = _create_poi(client, "Beat POI A")
        poi_d = _create_poi(client, "Beat POI D")

        shared = "This exact narration is reused verbatim at two places."
        beat_a = _create_beat(client, shared)
        beat_d = _create_beat(client, shared)

        assert beat_a["id"] != beat_d["id"], (
            "identical script_body must NOT collide into one shared beat node"
        )

        # Wire each beat to its own POI and confirm both survive independently.
        e_a = client.post(
            "/api/v1/edges/HAS_BEAT",
            json={
                "source": {"label": "POI", "id": poi_a["id"]},
                "target": {"label": "NarrativeBeat", "id": beat_a["id"]},
            },
        )
        e_d = client.post(
            "/api/v1/edges/HAS_BEAT",
            json={
                "source": {"label": "POI", "id": poi_d["id"]},
                "target": {"label": "NarrativeBeat", "id": beat_d["id"]},
            },
        )
        assert e_a.status_code == 201, e_a.text
        assert e_d.status_code == 201, e_d.text

        # Both beats still exist and retain the shared body.
        got_a = client.get(f"/api/v1/nodes/NarrativeBeat/{beat_a['id']}").json()
        got_d = client.get(f"/api/v1/nodes/NarrativeBeat/{beat_d['id']}").json()
        assert got_a["properties"]["script_body"] == shared
        assert got_d["properties"]["script_body"] == shared

        # POI-A's HAS_BEAT still points at beat_a (not silently rebound to D).
        edges = client.get(f"/api/v1/edges/HAS_BEAT?source_id={poi_a['id']}").json()
        targets = [e["target_id"] for e in edges["items"]]
        assert beat_a["id"] in targets
        assert beat_d["id"] not in targets

    def test_beat_create_generates_id(self, client):
        beat = _create_beat(client, "A beat that should get a generated id.")
        assert len(beat["id"]) > 10  # UUID-shaped


# ── A partial coordinate update is a 422, never an empty SET → 500 ──


@needs_neo4j
class TestDefect9PartialCoordinates:
    def test_poi_update_latitude_only_returns_422(self, client):
        """PUT {latitude: 45.0} with no longitude must be a clean 422, not a
        500 from an empty Cypher SET clause."""
        poi = _create_poi(client, "Partial Coord POI")
        resp = client.put(
            f"/api/v1/nodes/POI/{poi['id']}",
            json={"properties": {"latitude": 45.0}},
        )
        assert resp.status_code == 422, resp.text

    def test_poi_update_longitude_only_returns_422(self, client):
        poi = _create_poi(client, "Partial Lng POI")
        resp = client.put(
            f"/api/v1/nodes/POI/{poi['id']}",
            json={"properties": {"longitude": 3.0}},
        )
        assert resp.status_code == 422, resp.text

    def test_poi_update_both_coordinates_succeeds(self, client):
        poi = _create_poi(client, "Both Coord POI")
        resp = client.put(
            f"/api/v1/nodes/POI/{poi['id']}",
            json={"properties": {"latitude": 41.0, "longitude": -73.0}},
        )
        assert resp.status_code == 200, resp.text
        loc = resp.json()["properties"]["location"]
        assert abs(loc["lat"] - 41.0) < 0.001
        assert abs(loc["lng"] - (-73.0)) < 0.001

    def test_area_update_single_centroid_coord_returns_422(self, client):
        area = client.post(
            "/api/v1/nodes/Area",
            json={
                "name": "Partial Centroid Area",
                "area_type": "district",
                "city_name": "paris",
                "boundary": "POLYGON((2.35 48.85, 2.36 48.85, 2.36 48.86, 2.35 48.86, 2.35 48.85))",
                "centroid_lat": 48.8566,
                "centroid_lng": 2.3610,
            },
        ).json()
        resp = client.put(
            f"/api/v1/nodes/Area/{area['id']}",
            json={"properties": {"centroid_lat": 49.0}},
        )
        assert resp.status_code == 422, resp.text
