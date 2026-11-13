"""Unit tests for GET /api/v1/trips endpoint.

Trips are seeded directly through create_trip_with_stops (adapter-shaped stop
dicts built from the toy-seeded graph) — NOT through POST /trips/generate,
which runs the real tour engine whose density gate refuses the toy
corpus. The list read path is what's under test here, including its fallback
for legacy single-beat items (the seeded "Paris Spring 2026" trip).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.auth.tokens import create_access_token
from src.api.crud.trips import create_trip_with_stops
from src.connection import create_driver, get_database
from src.schema.constraints import apply_all
from src.seed.runner import seed_all
from src.seed.users import TEST_USER_EMAIL as SEED_USER_EMAIL
from tests.conftest import _assert_test_port, needs_neo4j


@pytest.fixture(scope="module")
def seeded_driver():
    """Create a driver, wipe DB, apply schema, and seed data."""
    # Defense-in-depth: refuse the DETACH DELETE unless NEO4J_URI is the test
    # port (7688), independent of the suite-wide pytest_configure guard.
    _assert_test_port()
    d = create_driver()
    with d.session(database=get_database()) as s:
        s.run("MATCH (n) DETACH DELETE n")
    apply_all(d)
    seed_all(d)
    yield d
    d.close()


@pytest.fixture(scope="module")
def seeded_user_id(seeded_driver):
    """The seeded User that owns the 'Mom'/'Kid' profiles."""
    with seeded_driver.session(database=get_database()) as s:
        return s.run(
            "MATCH (u:User {email: $email}) RETURN u.id AS id", email=SEED_USER_EMAIL
        ).single()["id"]


@pytest.fixture(scope="module")
def client(seeded_driver, seeded_user_id):
    """TestClient backed by the real Neo4j database with seed data.

    GET /trips is ownership-scoped since the 2026-07-19 IDOR fix (it used to
    serve ANY profile's trips to an anonymous caller), so the client
    authenticates as the User who owns the seeded profiles.
    """
    app = create_app()
    with TestClient(app) as c:
        token = create_access_token(seeded_user_id, SEED_USER_EMAIL)
        c.headers["Authorization"] = f"Bearer {token}"
        yield c


@pytest.fixture(scope="module")
def mom_profile_id(seeded_driver):
    """Get the profile ID for the 'Mom' profile from seed data."""
    with seeded_driver.session(database=get_database()) as s:
        result = s.run("MATCH (p:Profile {display_name: 'Mom'}) RETURN p.id AS id").single()
        return result["id"]


def _engine_shaped_stops(driver) -> list[dict]:
    """Adapter-shaped stop dicts from the toy-seeded graph — no engine run.

    Mirrors the route_script_to_stops output contract: all of a POI's beats in
    beat_ids, primary_beat_id = beat_ids[0], dominant lens (deterministic pick).
    """
    with driver.session(database=get_database()) as s:
        records = s.run(
            """
            MATCH (poi:POI)-[:HAS_BEAT]->(b:NarrativeBeat)
            OPTIONAL MATCH (b)-[:TAGGED_WITH]->(l:Lens)
            WITH poi, b, min(l.name) AS beat_lens
            ORDER BY b.id
            WITH poi, collect(b.id) AS beat_ids, min(beat_lens) AS lens_name
            RETURN poi.id AS poi_id,
                   poi.name AS poi_name,
                   poi.location.latitude AS lat,
                   poi.location.longitude AS lng,
                   coalesce(poi.importance_tier, 3) AS importance_tier,
                   beat_ids,
                   lens_name
            ORDER BY poi.name
            """
        ).data()
    return [
        {
            "sort_order": idx + 1,
            "poi_id": r["poi_id"],
            "poi_name": r["poi_name"],
            "lat": r["lat"],
            "lng": r["lng"],
            "beat_ids": r["beat_ids"],
            "primary_beat_id": r["beat_ids"][0],
            "lens_name": r["lens_name"],
            "duration_min": 30,
            "importance_tier": r["importance_tier"],
            "start_time": "09:00",
            "area": None,
            "dwell_seconds": 1800,
        }
        for idx, r in enumerate(records)
    ]


def _create_trip(driver, profile_id: str, name: str) -> tuple[str, list[dict]]:
    """Persist a trip via the crud layer; returns (trip_id, stops used)."""
    stops = _engine_shaped_stops(driver)
    assert stops, "toy seed must provide POIs with beats"
    with driver.session(database=get_database()) as s:
        result = create_trip_with_stops(
            s,
            trip_name=name,
            profile_id=profile_id,
            start_date="2026-05-01",
            end_date="2026-05-03",
            stops=stops,
        )
    return result["trip_id"], stops


@needs_neo4j
class TestListTripsEndpoint:
    """Integration tests for GET /api/v1/trips endpoint."""

    def test_list_trips_for_profile_returns_trips(self, client, seeded_driver, mom_profile_id):
        """After persisting a trip, GET /trips returns it."""
        trip_id, _ = _create_trip(seeded_driver, mom_profile_id, "List Test Trip")

        resp = client.get(f"/api/v1/trips?profile_id={mom_profile_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        trip_ids = [t["trip_id"] for t in data]
        assert trip_id in trip_ids

    def test_list_trips_for_profile_empty(self, client, seeded_driver):
        """A profile with no trips returns an empty list."""
        # Create a fresh profile with no trips
        with seeded_driver.session(database=get_database()) as s:
            s.run(
                """
                CREATE (p:Profile {
                    id: 'profile-no-trips',
                    display_name: 'Empty User'
                })
                """
            )
            # The route is ownership-scoped: an UNOWNED profile is (correctly)
            # a 404 now, so link it to the seeded caller to test the
            # owns-it-but-has-no-trips case this test is actually about.
            s.run(
                "MATCH (u:User {email: $email}), (p:Profile {id: 'profile-no-trips'}) "
                "MERGE (u)-[:HAS_PROFILE]->(p)",
                email=SEED_USER_EMAIL,
            )

        resp = client.get("/api/v1/trips?profile_id=profile-no-trips")
        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    def test_list_trips_includes_stops_with_poi_and_lens(
        self, client, seeded_driver, mom_profile_id
    ):
        """Trips in the list include full stop details with POI, beats, and lens data."""
        trip_id, seeded_stops = _create_trip(seeded_driver, mom_profile_id, "Stop Detail Trip")

        resp = client.get(f"/api/v1/trips?profile_id={mom_profile_id}")
        assert resp.status_code == 200
        data = resp.json()

        trip = next((t for t in data if t["trip_id"] == trip_id), None)
        assert trip is not None
        assert trip["total_stops"] == len(seeded_stops)
        assert len(trip["stops"]) == trip["total_stops"]

        # One row per item — multi-beat stops must NOT fan out into duplicates.
        by_order = {s["sort_order"]: s for s in trip["stops"]}
        assert len(by_order) == len(trip["stops"])

        for seeded in seeded_stops:
            stop = by_order[seeded["sort_order"]]
            assert stop["poi_id"] == seeded["poi_id"]
            assert sorted(stop["beat_ids"]) == sorted(seeded["beat_ids"])
            assert stop["beat_id"] == seeded["primary_beat_id"]
            assert stop["lens_name"] == seeded["lens_name"]
            for field in ("poi_name", "lat", "lng", "lens_display", "duration_min",
                          "importance_tier", "start_time"):
                assert field in stop

    def test_list_trips_reads_legacy_single_beat_items(self, client, mom_profile_id):
        """The seeded 'Paris Spring 2026' trip is a legacy trip: items carry one
        PLAYS_BEAT edge and no beat_ids/primary_beat_id/lens_name properties.
        The read path must fall back to edge-derived values."""
        resp = client.get(f"/api/v1/trips?profile_id={mom_profile_id}")
        assert resp.status_code == 200
        legacy = next(
            (t for t in resp.json() if t["trip_name"] == "Paris Spring 2026"), None
        )
        assert legacy is not None, "seed_all should have created 'Paris Spring 2026'"
        assert legacy["total_stops"] > 0
        for stop in legacy["stops"]:
            assert stop["beat_id"], "legacy item must derive beat_id from its edge"
            assert stop["beat_ids"] == [stop["beat_id"]]

    def test_list_trips_returns_stop_with_orphaned_beat(self, seeded_driver):
        """A stop whose only beat is later deleted (no surviving PLAYS_BEAT edge)
        must still be returned by list_trips_for_profile — with an empty beats
        list — and total_stops / total_duration_min must count it, not silently
        drop it.

        Uses a dedicated profile + a POI whose beat exists nowhere else, so the
        DETACH DELETE severs exactly one trip's edge and never touches beats other
        trips in this shared-seed module depend on. Calls the crud read path
        directly (not the HTTP route) — the defect is that the Cypher REQUIRED a
        PLAYS_BEAT edge and dropped the row when none survived.
        """
        from src.api.crud.nodes import delete_node
        from src.api.crud.trips import list_trips_for_profile

        with seeded_driver.session(database=get_database()) as s:
            # Isolated fixture: own profile, own POI, own beat.
            s.run(
                """
                CREATE (p:Profile {id: 'profile-orphan-beat', display_name: 'Orphan User'})
                CREATE (poi:POI {id: 'poi-orphan-beat', name: 'Orphan Square',
                                 importance_tier: 3,
                                 location: point({latitude: 48.85, longitude: 2.35})})
                CREATE (b:NarrativeBeat {id: 'beat-orphan-only', script_body: 'Once here.',
                                        duration_sec: 42})
                CREATE (poi)-[:HAS_BEAT]->(b)
                """
            )
            stop = {
                "sort_order": 1,
                "poi_id": "poi-orphan-beat",
                "poi_name": "Orphan Square",
                "lat": 48.85,
                "lng": 2.35,
                "beat_ids": ["beat-orphan-only"],
                "primary_beat_id": "beat-orphan-only",
                "lens_name": None,
                "duration_min": 30,
                "importance_tier": 3,
                "start_time": "09:00",
            }
            result = create_trip_with_stops(
                s,
                trip_name="Orphaned Beat Trip",
                profile_id="profile-orphan-beat",
                start_date="2026-05-01",
                end_date="2026-05-03",
                stops=[stop],
            )
            trip_id = result["trip_id"]

            # Delete the beat, severing the stop's only PLAYS_BEAT edge.
            assert delete_node(s, "NarrativeBeat", "beat-orphan-only") is True

            trips = list_trips_for_profile(s, "profile-orphan-beat")

        trip = next((t for t in trips if t["trip_id"] == trip_id), None)
        assert trip is not None
        # The orphaned stop must NOT be dropped.
        assert trip["total_stops"] == 1
        assert len(trip["stops"]) == 1
        assert trip["total_duration_min"] == 30
        orphan = trip["stops"][0]
        assert orphan["poi_id"] == "poi-orphan-beat"
        assert orphan["stop_id"]
        # Stored item properties (not the vanished edge) populate the stop.
        assert orphan["beat_id"] == "beat-orphan-only"
        assert orphan["beat_ids"] == ["beat-orphan-only"]

    def test_list_trips_requires_valid_profile_id(self, client):
        """A nonexistent profile returns 404."""
        resp = client.get("/api/v1/trips?profile_id=nonexistent-profile-999")
        assert resp.status_code == 404

    def test_create_trip_persists_per_stop_narration(self, seeded_driver, mom_profile_id):
        """create_trip_with_stops writes each stop's `narration` onto the
        ItineraryItem; reading the property back equals the input string."""
        stops = _engine_shaped_stops(seeded_driver)[:1]
        assert stops, "toy seed must provide a POI with beats"
        expected = "Settle in. This is the stitched opening. Now walk on."
        stops[0]["narration"] = expected

        with seeded_driver.session(database=get_database()) as s:
            result = create_trip_with_stops(
                s,
                trip_name="Narration Persist Test",
                profile_id=mom_profile_id,
                start_date="2026-05-01",
                end_date="2026-05-03",
                stops=stops,
            )

        with seeded_driver.session(database=get_database()) as s:
            rec = s.run(
                "MATCH (t:Trip {id: $tid})-[:HAS_STOP]->(i:ItineraryItem) "
                "RETURN i.narration AS narration ORDER BY i.sort_order",
                tid=result["trip_id"],
            ).single()

        assert rec is not None
        assert rec["narration"] == expected

    def test_list_trips_surfaces_stop_id_and_per_stop_audio(
        self, client, seeded_driver, mom_profile_id, monkeypatch, tmp_path
    ):
        """GET /trips exposes each stop's ItineraryItem id (stop_id) and,
        once per-stop audio is generated, prefers item.audio_url over the beat's —
        so mobile can address + play per-stop narration."""
        monkeypatch.setenv("AUDIO_STORAGE_PATH", str(tmp_path))
        monkeypatch.setenv("AUDIO_STORAGE", "local")

        stops = _engine_shaped_stops(seeded_driver)
        assert stops
        for s in stops:
            s["narration"] = f"Stop {s['sort_order']}. Welcome here. Walk on."
        with seeded_driver.session(database=get_database()) as s:
            result = create_trip_with_stops(
                s,
                trip_name="Per-stop audio trip",
                profile_id=mom_profile_id,
                start_date="2026-05-01",
                end_date="2026-05-03",
                stops=stops,
            )
        trip_id = result["trip_id"]

        gen = client.post(
            f"/api/v1/audio/generate-trip-stops/{trip_id}", json={"provider": "mock"}
        )
        assert gen.status_code == 200, gen.text
        assert gen.json()["generated"] == len(stops)

        resp = client.get(f"/api/v1/trips?profile_id={mom_profile_id}")
        assert resp.status_code == 200
        trip = next((t for t in resp.json() if t["trip_id"] == trip_id), None)
        assert trip is not None
        for stop in trip["stops"]:
            assert stop["stop_id"], "ItineraryItem id must be surfaced for per-stop addressing"
            assert stop["audio_url"] and "stops/" in stop["audio_url"], (
                "GET /trips must surface the per-stop narration audio, not the beat's"
            )

    def test_list_trips_surfaces_narration_text(self, client, seeded_driver, mom_profile_id):
        """GET /trips exposes each stop's stitched narration TEXT so it can be
        read/verified on web (curl / preview page), not just played."""
        stops = _engine_shaped_stops(seeded_driver)
        assert stops
        expected = {
            s["sort_order"]: f"Stop {s['sort_order']}. The story for this stop. Walk on."
            for s in stops
        }
        for s in stops:
            s["narration"] = expected[s["sort_order"]]
        with seeded_driver.session(database=get_database()) as s:
            result = create_trip_with_stops(
                s,
                trip_name="Narration surface trip",
                profile_id=mom_profile_id,
                start_date="2026-05-01",
                end_date="2026-05-03",
                stops=stops,
            )

        resp = client.get(f"/api/v1/trips?profile_id={mom_profile_id}")
        assert resp.status_code == 200
        trip = next((t for t in resp.json() if t["trip_id"] == result["trip_id"]), None)
        assert trip is not None
        for stop in trip["stops"]:
            assert stop["narration"] == expected[stop["sort_order"]]
