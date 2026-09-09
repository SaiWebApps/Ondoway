"""Tests for the public read-only product API — GET /api/v1/lenses and
GET /api/v1/profile.

Both are the mobile client's read surface: unlike the workbench CRUD routers,
they are mounted unconditionally (outside `_workbench_api_enabled()`). See
specs/2026-08-10-profile-endpoint/design.md for the /profile contract.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.api.auth.tokens import create_access_token
from src.connection import get_database
from src.schema.definitions import DAG_CHILD_LENSES, MVP_LENSES
from src.seed.lenses import seed_lenses


@pytest.fixture(scope="module")
def seeded_client(client, clean_driver):
    """The shared `client` fixture with the canonical lens taxonomy seeded."""
    seed_lenses(clean_driver)
    return client


class TestLensesEndpoint:
    def test_returns_200_non_empty_without_auth(self, seeded_client):
        """AC-1: no Authorization header -> 200, non-empty body (a [] fails)."""
        response = seeded_client.get("/api/v1/lenses")
        assert response.status_code == 200
        body = response.json()
        assert body != []
        assert len(body) > 0

    def test_all_eight_parents_with_fields(self, seeded_client):
        """AC-2: all 8 parent lenses by name (== MVP_LENSES), each carrying
        id, name, display_label, is_parent:true.
        """
        response = seeded_client.get("/api/v1/lenses")
        assert response.status_code == 200
        body = response.json()

        parents = [item for item in body if item.get("is_parent") is True]
        assert len(parents) == 8

        expected_names = {lens["name"] for lens in MVP_LENSES}
        actual_names = {parent["name"] for parent in parents}
        assert actual_names == expected_names

        expected_by_name = {lens["name"]: lens for lens in MVP_LENSES}
        for parent in parents:
            assert set(parent.keys()) >= {"id", "name", "display_label", "is_parent"}
            assert isinstance(parent["id"], str) and parent["id"]
            assert parent["display_label"] == expected_by_name[parent["name"]]["display_label"]
            assert parent["is_parent"] is True

    def test_nested_children_all_21_under_correct_parent(self, seeded_client):
        """AC-3/AC-4: all 21 universal child lenses (id, name, display_label,
        is_parent:false) nested under their correct parent's `children` key,
        per DAG_CHILD_LENSES.parent_name.

        AC-4 (pinned contract): the parent and child key sets are asserted as
        an EXACT snapshot (``==``), not a floor (``>=``). An undocumented extra
        key on any parent or child object fails this test, so the client
        repoint slice has a fixed target. Superset of canonical children is
        tolerated via the ``in expected`` guard below;
        ``test_superset_extra_city_child_tolerated`` exercises that path with a
        real non-canonical node.
        """
        response = seeded_client.get("/api/v1/lenses")
        assert response.status_code == 200
        body = response.json()

        expected_by_child_name = {child["name"]: child for child in DAG_CHILD_LENSES}
        assert len(expected_by_child_name) == 21

        # AC-4 pinned key snapshot — EXACT, not a floor.
        expected_parent_keys = {"id", "name", "display_label", "is_parent", "children"}
        expected_child_keys = {"id", "name", "display_label", "is_parent"}

        seen_children: dict[str, dict] = {}
        for parent in body:
            assert set(parent.keys()) == expected_parent_keys
            assert isinstance(parent["children"], list)
            for child in parent["children"]:
                assert set(child.keys()) == expected_child_keys
                assert child["is_parent"] is False
                if child["name"] in expected_by_child_name:
                    seen_children[child["name"]] = {**child, "_under_parent": parent["name"]}

        assert set(seen_children.keys()) == set(expected_by_child_name.keys())

        for child_name, expected in expected_by_child_name.items():
            actual = seen_children[child_name]
            assert isinstance(actual["id"], str) and actual["id"]
            assert actual["display_label"] == expected["display_label"]
            assert actual["_under_parent"] == expected["parent_name"]

    def test_superset_extra_city_child_tolerated(self, seeded_client, clean_driver):
        """AC-3 tail clause: a non-canonical (city-specific) child lens under a
        real parent must NOT break the endpoint. Exercises the superset path
        directly — seed_lenses forbids non-canonical nodes, so this test injects
        one after seeding. Expect: 200, all 21 canonical children still present
        (none dropped), and the extra child represented honestly (correct real
        parent, no fabricated parent, same 4-key child shape). Cleans up after
        itself so the module DB stays canonical for order-independent runs.
        """
        with clean_driver.session(database=get_database()) as s:
            s.run(
                "MERGE (child:Lens {name: $name}) "
                "SET child.id = coalesce(child.id, randomUUID()), "
                "    child.display_label = $label, child.is_parent = false "
                "WITH child "
                "MATCH (parent:Lens {name: $parent}) "
                "MERGE (parent)-[r:IS_PARENT_OF]->(child) "
                "SET r.id = coalesce(r.id, randomUUID())",
                name="film_locations_paris",
                label="Paris Film Locations",
                parent="arts_culture",
            )
        try:
            response = seeded_client.get("/api/v1/lenses")
            assert response.status_code == 200
            body = response.json()

            children_by_parent = {parent["name"]: parent["children"] for parent in body}

            # All 21 canonical children still present — the extra node did not
            # cause any canonical child to be dropped or a 500.
            canonical_child_names = {child["name"] for child in DAG_CHILD_LENSES}
            all_child_names = {
                child["name"] for kids in children_by_parent.values() for child in kids
            }
            assert canonical_child_names <= all_child_names

            # The extra city child appears under its real parent, honestly shaped,
            # not under a fabricated one.
            arts_children = {c["name"]: c for c in children_by_parent["arts_culture"]}
            assert "film_locations_paris" in arts_children
            extra = arts_children["film_locations_paris"]
            assert set(extra.keys()) == {"id", "name", "display_label", "is_parent"}
            assert extra["is_parent"] is False
            assert extra["display_label"] == "Paris Film Locations"
        finally:
            with clean_driver.session(database=get_database()) as s:
                s.run(
                    "MATCH (l:Lens {name: $name}) DETACH DELETE l",
                    name="film_locations_paris",
                )

    def test_degraded_data_returns_200_and_honest(self, seeded_client, clean_driver):
        """AC-5 (negative): degraded graph -> 200, no 500, honest representation.

        Two degraded conditions injected directly (bypassing seed_lenses, which
        forbids non-canonical nodes):
          - an orphan child Lens with NO IS_PARENT_OF edge from any parent
          - a childless (but real, is_parent:true) parent Lens with zero children

        Expect: 200 (not a 500); the childless parent is present with
        children: [] (not dropped, not crashed); the orphan child is not
        fabricated under any parent's children (no invented relationship);
        and an existing canonical parent's child count is unaffected by the
        orphan's mere presence in the graph (rules out a Cartesian-product
        bug where an unrelated OPTIONAL MATCH duplicates children).
        """
        with clean_driver.session(database=get_database()) as s:
            before = s.run(
                "MATCH (p:Lens {name: 'arts_culture'})-[:IS_PARENT_OF]->(c:Lens) "
                "RETURN count(c) AS n"
            ).single()["n"]

            s.run(
                "MERGE (child:Lens {name: $name}) "
                "SET child.id = coalesce(child.id, randomUUID()), "
                "    child.display_label = $label, child.is_parent = false",
                name="orphan_child_test",
                label="Orphan Child",
            )
            s.run(
                "MERGE (parent:Lens {name: $name}) "
                "SET parent.id = coalesce(parent.id, randomUUID()), "
                "    parent.display_label = $label, parent.is_parent = true",
                name="childless_parent_test",
                label="Childless Parent",
            )

        try:
            response = seeded_client.get("/api/v1/lenses")
            assert response.status_code == 200
            body = response.json()

            by_name = {item["name"]: item for item in body}

            # Childless parent: present, honestly empty — not dropped, not a 500.
            assert "childless_parent_test" in by_name
            assert by_name["childless_parent_test"]["children"] == []

            # Orphan child: never fabricated under any parent.
            for parent in body:
                child_names = {c["name"] for c in parent["children"]}
                assert "orphan_child_test" not in child_names

            # Canonical taxonomy unaffected — no Cartesian-product duplication
            # caused by the orphan's presence in the graph.
            assert len(by_name["arts_culture"]["children"]) == before
        finally:
            with clean_driver.session(database=get_database()) as s:
                s.run(
                    "MATCH (l:Lens) WHERE l.name IN $names DETACH DELETE l",
                    names=["orphan_child_test", "childless_parent_test"],
                )


# ---------------------------------------------------------------------------
# GET /api/v1/cities/{slug}/hours — Docs/adr/0006. The map's hours are
# share-alike, so what we hold goes back out where anyone can check it.
# ---------------------------------------------------------------------------


class TestCityHoursFile:
    """M8: the hours we read, mix and speak are published back with their credit.

    Docs/adr/0006: "Because map hours mix with guesses, the OpenStreetMap
    share-alike licence applies: each city's hours file is published openly and
    the app carries a credit line." Two things a reader must be able to do: get
    the file without an account, and tell a MAP hour from a GUESS — the
    distinction the whole phase turns on, and the one a flattened file would
    republish as if the map had said it.

    UNDO: drop `source` from the row -> a guess reads as the map's -> RED.
    """

    def _seed_doors(self, driver):
        with driver.session(database=get_database()) as s:
            s.run(
                "MERGE (p:POI {id: 'hours-mapped'}) SET p.city_name = 'paris', "
                "p.name = 'Mapped Museum', p.gated = true, "
                "p.opening_hours = 'Tu-Su 09:30-18:00', p.opening_hours_source = 'map'"
            )
            s.run(
                "MERGE (p:POI {id: 'hours-guessed'}) SET p.city_name = 'paris', "
                "p.name = 'Guessed Chapel', p.gated = true, "
                "p.opening_hours = 'Mo-Su 10:00-17:00', p.opening_hours_source = 'guess'"
            )
            s.run(
                "MERGE (p:POI {id: 'hours-open-square'}) SET p.city_name = 'paris', "
                "p.name = 'Open Square', p.gated = false"
            )

    def _drop_doors(self, driver):
        with driver.session(database=get_database()) as s:
            s.run(
                "MATCH (p:POI) WHERE p.id STARTS WITH 'hours-' DETACH DELETE p"
            )

    def test_the_file_is_public_and_says_where_each_hour_came_from(
        self, seeded_client, clean_driver
    ):
        self._seed_doors(clean_driver)
        try:
            response = seeded_client.get("/api/v1/cities/paris/hours")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["license"] == "© OpenStreetMap contributors, ODbL"
            by_id = {door["poi_id"]: door for door in body["doors"]}
            assert by_id["hours-mapped"]["source"] == "map"
            assert by_id["hours-mapped"]["opening_hours"] == "Tu-Su 09:30-18:00"
            assert by_id["hours-guessed"]["source"] == "guess", (
                "a guess must not be republished as if the map had said it"
            )
            # A place you do not go inside has no door and no hours to publish.
            assert "hours-open-square" not in by_id
            assert body["door_count"] == len(body["doors"])
        finally:
            self._drop_doors(clean_driver)

    def test_an_unknown_city_is_a_404_not_an_empty_file(self, seeded_client):
        """An empty file reads as "this city has no doors", which is a claim we
        would be making about a city we do not have."""
        response = seeded_client.get("/api/v1/cities/atlantis/hours")
        assert response.status_code == 404
        assert response.json().get("detail")


# ---------------------------------------------------------------------------
# GET /api/v1/profile (bearer) — specs/2026-08-10-profile-endpoint/design.md.
# The module DB is not wiped between tests, so each test uses distinct user ids.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def profile_client(client, clean_driver):
    """Client with the canonical lens taxonomy seeded — child lenses must exist
    for PREFERS_LENS. Per-test users/profiles are seeded inside each test."""
    seed_lenses(clean_driver)
    return client


def _seed_user(driver, user_id, email):
    with driver.session(database=get_database()) as s:
        s.run("MERGE (u:User {id: $uid}) SET u.email = $email", uid=user_id, email=email)


def _seed_profile(driver, user_id, *, profile_id, display_name, lens_names, created_at, theme=None):
    """Seed one Profile mirroring the real writer (auth/routes.py:441): id +
    created_at + display_name, optional theme_preference (None -> property
    absent, since SET x = null removes it), and PREFERS_LENS to child lenses."""
    with driver.session(database=get_database()) as s:
        s.run(
            "MATCH (u:User {id: $uid}) "
            "CREATE (u)-[:HAS_PROFILE]->(p:Profile {id: $pid, display_name: $name, "
            "created_at: datetime($created)}) "
            "SET p.theme_preference = $theme",
            uid=user_id, pid=profile_id, name=display_name, created=created_at, theme=theme,
        )
        for ln in lens_names:
            s.run(
                "MATCH (p:Profile {id: $pid}), (l:Lens {name: $ln}) "
                "MERGE (p)-[:PREFERS_LENS]->(l)",
                pid=profile_id, ln=ln,
            )


def _lens_ids(driver, names):
    with driver.session(database=get_database()) as s:
        rec = s.run(
            "MATCH (l:Lens) WHERE l.name IN $names RETURN collect(l.id) AS ids",
            names=names,
        ).single()
        return set(rec["ids"])


def _auth(user_id, email):
    return {"Authorization": f"Bearer {create_access_token(user_id, email)}"}


class TestProfileEndpoint:
    def test_missing_or_invalid_bearer_returns_401(self, profile_client):
        """AC-10: no header, and a malformed/expired token, all 401 with no leak."""
        no_header = profile_client.get("/api/v1/profile")
        assert no_header.status_code == 401
        assert "display_name" not in no_header.text
        assert "selected_lens_ids" not in no_header.text

        garbage = profile_client.get(
            "/api/v1/profile", headers={"Authorization": "Bearer garbage.token.here"}
        )
        assert garbage.status_code == 401
        assert "display_name" not in garbage.text

        with patch("src.api.auth.tokens.ACCESS_TOKEN_EXPIRE_MINUTES", -1):
            expired = create_access_token("user-p-401", "p401@ondoway.app")
        expired_resp = profile_client.get(
            "/api/v1/profile", headers={"Authorization": f"Bearer {expired}"}
        )
        assert expired_resp.status_code == 401
        assert "selected_lens_ids" not in expired_resp.text

    def test_no_profile_returns_404_not_empty(self, profile_client, clean_driver):
        """AC-8: authenticated user with zero HAS_PROFILE -> 404 (not {}, not 500)."""
        uid, email = "user-noprofile", "noprofile@ondoway.app"
        _seed_user(clean_driver, uid, email)
        resp = profile_client.get("/api/v1/profile", headers=_auth(uid, email))
        assert resp.status_code == 404
        assert "display_name" not in resp.text  # no fabricated empty profile
        assert resp.json().get("detail")

    def test_returns_profile_with_display_name_and_lens_set(self, profile_client, clean_driver):
        """AC-6: one profile + child lenses -> 200 with display_name, profile_id,
        and selected_lens_ids == exactly the profile's child-lens id set."""
        uid, email = "user-ac6", "ac6@ondoway.app"
        _seed_user(clean_driver, uid, email)
        lenses = ["hidden_history", "dark_history", "street_art"]
        _seed_profile(clean_driver, uid, profile_id="prof-ac6", display_name="adam",
                      lens_names=lenses, created_at="2026-08-01T00:00:00")
        resp = profile_client.get("/api/v1/profile", headers=_auth(uid, email))
        assert resp.status_code == 200
        body = resp.json()
        assert body["profile_id"] == "prof-ac6"
        assert body["display_name"] == "adam"
        assert set(body["selected_lens_ids"]) == _lens_ids(clean_driver, lenses)

    def test_theme_preference_verbatim_or_null(self, profile_client, clean_driver):
        """AC-7: theme_preference verbatim when set, JSON null (key present) when absent."""
        uid_s, email_s = "user-theme-set", "themeset@ondoway.app"
        _seed_user(clean_driver, uid_s, email_s)
        _seed_profile(clean_driver, uid_s, profile_id="prof-theme-set", display_name="t",
                      lens_names=["hidden_history"], created_at="2026-08-01T00:00:00", theme="dark")
        set_resp = profile_client.get("/api/v1/profile", headers=_auth(uid_s, email_s))
        assert set_resp.json()["theme_preference"] == "dark"

        uid_a, email_a = "user-theme-absent", "themeabsent@ondoway.app"
        _seed_user(clean_driver, uid_a, email_a)
        _seed_profile(clean_driver, uid_a, profile_id="prof-theme-absent", display_name="t",
                      lens_names=["hidden_history"], created_at="2026-08-01T00:00:00", theme=None)
        absent = profile_client.get("/api/v1/profile", headers=_auth(uid_a, email_a)).json()
        assert "theme_preference" in absent  # key present
        assert absent["theme_preference"] is None

    def test_multi_profile_returns_latest_created_at(self, profile_client, clean_driver):
        """AC-9: two profiles of distinct created_at -> return the later one and
        ITS lens set; the older's lens set must not appear."""
        uid, email = "user-ac9", "ac9@ondoway.app"
        _seed_user(clean_driver, uid, email)
        old_lenses = ["hidden_history"]
        new_lenses = ["street_art", "dark_history"]
        _seed_profile(clean_driver, uid, profile_id="prof-old", display_name="old",
                      lens_names=old_lenses, created_at="2026-01-01T00:00:00")
        _seed_profile(clean_driver, uid, profile_id="prof-new", display_name="new",
                      lens_names=new_lenses, created_at="2026-08-01T00:00:00")
        body = profile_client.get("/api/v1/profile", headers=_auth(uid, email)).json()
        assert body["profile_id"] == "prof-new"
        assert body["display_name"] == "new"
        assert set(body["selected_lens_ids"]) == _lens_ids(clean_driver, new_lenses)
        old_only = _lens_ids(clean_driver, old_lenses) - _lens_ids(clean_driver, new_lenses)
        assert old_only.isdisjoint(set(body["selected_lens_ids"]))
