"""POST /trips/{id}/compose serves stops from stored place recipes, through HTTP.

The corpus is the two-place walk ``tests/test_recipe_reuse.py`` builds (the tower has
a second story, the bridge closes the day). Its places, beats and recipes live in the
test graph; the recipes are built by the product's build job with the test writer and
voice from ``tests/test_place_recipes.py``. Walking legs come from the deterministic
routing double in ``tests/test_trip_preview_contract.py``. The route, the planner, the
reuse rules, the gates, the graph writes and the links are the product's own code.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from src.api.auth.tokens import create_access_token
from src.api.dependencies import get_premium_compose_executor
from src.api.routes import trips as trips_route
from src.connection import get_database
from src.tour.generation import GLUE_CLOSING
from src.tour.premium_tour import PremiumBuildIdentity, exact_snapshot_sha256
from src.tour.recipe_build import build_recipes, pinned_voice_identity, places_to_build
from src.tour.verify import MockFaithfulnessChecker
from tests.test_place_recipes import _seed_graph, _Voice, _Writer
from tests.test_recipe_reuse import _tower_first
from tests.test_trip_api import _hallucinate_defect
from tests.test_trip_preview_contract import _FakeRoutingClient

TRIP_ID = "compose-recipes-trip"
USER_ID = "compose-recipes-user"
EMAIL = "compose-recipes@example.test"
PROFILE_ID = "compose-recipes-profile"
#: The tower is told from its recipe; the bridge is the day's last stop.
TOWER, BRIDGE = 0, 1
TOWER_RECIPE = "tower:no-lens:solo:outside"
#: A registered city, because the day's contingency set reads the city's country.
CITY = "paris"


class _FreshWriter(_Writer):
    """The test writer with a close of its own, so a telling written during compose
    reads differently from the stored one. It records each call's stop and whether
    it asked for a longer telling (a request that carries what was already told)."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple[int, bool]] = []

    def execute(self, unit):
        self.asked.append((unit.stop_index, bool(unit.authorized_request.already_told)))
        response = super().execute(unit)
        body = json.loads(response.body)
        for sentence in body["sentences"]:
            if sentence["source_id"] == GLUE_CLOSING:
                sentence["text"] = f"That's {unit.poi_name}, freshly told."
        return dataclasses.replace(response, body=json.dumps(body, sort_keys=True).encode("utf-8"))


def _walk():
    """The two-place walk with the bridge a short stroll from the tower, so the day's
    walking stays within what its spoken minutes cover."""
    snapshot = _tower_first()
    tower, bridge = snapshot.pois
    near = bridge.model_copy(update={"lat": tower.lat + 0.0003, "lng": tower.lng})
    return dataclasses.replace(snapshot, pois=(tower, near))


def _bearer() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(USER_ID, EMAIL)}"}


def _seed_trip(session) -> None:
    """A solo, no-lens trip through the tower then the bridge, with the tower priced
    long enough to hold a longer telling."""
    tour_input = {
        "start": [40.7000, -74.0000],
        "end": None,
        "duration_min": 90,
        "city_slug": CITY,
        "lenses": None,
        "round_trip": False,
        "narration_register": "solo",
        "start_time": "10:00",
    }
    options = [
        {
            "poi_ids": ["tower", "bridge"],
            "visit_goes_inside": {"tower": False, "bridge": False},
            "planned_visit_seconds": {"tower": 1200, "bridge": 300},
        }
    ]
    session.run(
        "MERGE (u:User {id: $uid}) SET u.email = $email "
        "MERGE (p:Profile {id: $pid}) SET p.display_name = 'Compose recipes' "
        "MERGE (u)-[:HAS_PROFILE]->(p) "
        "CREATE (t:Trip {id: $tid, name: 'Compose recipes', status: 'planning', "
        "                created_at: datetime(), tour_input_json: $ti, options_json: $oj}) "
        "MERGE (p)-[:IS_CAPTAIN_OF]->(t)",
        uid=USER_ID,
        email=EMAIL,
        pid=PROFILE_ID,
        tid=TRIP_ID,
        ti=json.dumps(tour_input),
        oj=json.dumps(options),
    )


@pytest.fixture()
def graph(client, clean_driver, monkeypatch):
    """The test graph holding the walk's places, their built recipes and the trip;
    compose reads the same snapshot and walks the deterministic legs."""
    snapshot = _walk()
    monkeypatch.setattr(trips_route, "load_paris_corpus", lambda *_a, **_k: snapshot)
    monkeypatch.setattr(trips_route, "RoutingClient", _FakeRoutingClient)
    monkeypatch.setattr(
        trips_route,
        "resolve_build_identity",
        lambda: PremiumBuildIdentity(commit_sha="1" * 40),
    )
    with clean_driver.session(database=get_database()) as s:
        _seed_graph(s, snapshot)
        outcomes = list(
            build_recipes(
                s,
                snapshot,
                places_to_build(snapshot),
                city_slug=CITY,
                snapshot_sha256=exact_snapshot_sha256(snapshot),
                executor=_Writer(),
                faithfulness_checker=MockFaithfulnessChecker(),
                voice=_Voice(),
                voice_identity=pinned_voice_identity(),
                build_identity=PremiumBuildIdentity(commit_sha="0" * 40),
                dry_run=False,
                should_continue=lambda _outcome: True,
            )
        )
        assert {o.recipe_id: o.status for o in outcomes}[TOWER_RECIPE] == "built"
        _seed_trip(s)
    return clean_driver


def _compose(client, writer: _FreshWriter):
    client.app.dependency_overrides[get_premium_compose_executor] = lambda: writer
    try:
        return client.post(
            f"/api/v1/trips/{TRIP_ID}/compose",
            json={"route_id": f"{TRIP_ID}-opt1"},
            headers=_bearer(),
        )
    finally:
        client.app.dependency_overrides.pop(get_premium_compose_executor, None)


def _stored_texts(driver) -> tuple[str, str]:
    """The tower recipe's stored short and longer tellings, each as one text."""
    with driver.session(database=get_database()) as s:
        row = s.run(
            "MATCH (r:PlaceRecipe {id: $id}) "
            "RETURN r.sentences_json AS short, r.full_sentences_json AS full",
            id=TOWER_RECIPE,
        ).single()
    return tuple(" ".join(s["text"] for s in json.loads(row[key])) for key in ("short", "full"))


def _refuse_stored(driver, field: str) -> None:
    """Rewrite one stored telling so its first sentence cites a beat the corpus does
    not have, which the tour's checks refuse."""
    with driver.session(database=get_database()) as s:
        stored = s.run(
            f"MATCH (r:PlaceRecipe {{id: $id}}) RETURN r.{field} AS j", id=TOWER_RECIPE
        ).single()["j"]
        s.run(
            f"MATCH (r:PlaceRecipe {{id: $id}}) SET r.{field} = $j",
            id=TOWER_RECIPE,
            j=json.dumps(_hallucinate_defect(json.loads(stored))),
        )


def _saved_item(driver, item_id: str) -> dict:
    """What the graph holds for one composed stop, with the recipe it links to."""
    with driver.session(database=get_database()) as s:
        return (
            s.run(
                "MATCH (i:ItineraryItem {id: $id}) "
                "OPTIONAL MATCH (i)-[:TOLD_FROM]->(r:PlaceRecipe) "
                "RETURN i.narration AS narration, i.full_narration AS full_narration, "
                "       r.id AS recipe",
                id=item_id,
            )
            .single()
            .data()
        )


def test_compose_serves_a_matching_stop_from_its_stored_recipe(client, graph):
    short, full = _stored_texts(graph)
    writer = _FreshWriter()

    resp = _compose(client, writer)

    assert resp.status_code == 200, resp.text
    assert writer.asked == [(BRIDGE, False)], "only the stop with no recipe is written"
    tower, bridge = resp.json()["stops"]
    assert tower["narration"].endswith(" " + short)
    assert tower["full_narration"] == full, "the stored longer telling is served too"
    saved = _saved_item(graph, tower["stop_id"])
    assert saved == {
        "narration": tower["narration"],
        "full_narration": full,
        "recipe": TOWER_RECIPE,
    }
    assert _saved_item(graph, bridge["stop_id"])["recipe"] is None


def test_a_stored_telling_the_checks_refuse_is_written_fresh_and_not_linked(client, graph):
    stored_short, stored_full = _stored_texts(graph)
    _refuse_stored(graph, "sentences_json")
    writer = _FreshWriter()

    resp = _compose(client, writer)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["attempts"] == 2, "the stored telling was served, refused and rewritten"
    assert sorted(writer.asked) == [(TOWER, False), (TOWER, True), (BRIDGE, False)]
    tower = body["stops"][TOWER]
    assert tower["narration"].endswith("That's Test Tower, freshly told.")
    assert stored_short not in tower["narration"]
    assert tower["full_narration"] and tower["full_narration"] != stored_full
    assert tower["full_narration"].endswith("That's Test Tower, freshly told.")
    saved = _saved_item(graph, tower["stop_id"])
    assert saved == {
        "narration": tower["narration"],
        "full_narration": tower["full_narration"],
        "recipe": None,
    }, "a rewritten stop is not told from its recipe"


def test_a_stored_longer_telling_the_checks_refuse_is_written_fresh(client, graph):
    stored_short, stored_full = _stored_texts(graph)
    _refuse_stored(graph, "full_sentences_json")
    writer = _FreshWriter()

    resp = _compose(client, writer)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(writer.asked) == [(TOWER, True), (BRIDGE, False)], (
        "the short telling still comes from the recipe; only the longer one is written"
    )
    tower = body["stops"][TOWER]
    assert tower["narration"].endswith(" " + stored_short)
    assert tower["full_narration"].endswith("That's Test Tower, freshly told.")
    assert tower["full_narration"] != stored_full
    assert _saved_item(graph, tower["stop_id"]) == {
        "narration": tower["narration"],
        "full_narration": tower["full_narration"],
        "recipe": TOWER_RECIPE,
    }
    assert not any(row["kind"] == "full_telling_dropped" for row in body["degradations"]), (
        "a refused stored telling the writer replaces is not reported as a loss"
    )
