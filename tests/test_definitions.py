"""Unit tests for schema definitions — no Neo4j required."""

from src.schema.definitions import (
    DAG_CHILD_LENSES,
    INDEXES,
    MVP_LENSES,
    RELATIONSHIP_TYPES,
    TAGGABLE_LENSES,
    UNIQUE_CONSTRAINTS,
)


class TestUniqueConstraints:
    def test_count_matches_schema_v3(self):
        """Schema_v3 §3 plus the family group: 11 unique constraints."""
        assert len(UNIQUE_CONSTRAINTS) == 11

    def test_constraint_names_are_unique(self):
        names = [c.name for c in UNIQUE_CONSTRAINTS]
        assert len(names) == len(set(names))

    def test_user_has_id_and_email_constraints(self):
        user_props = {c.property for c in UNIQUE_CONSTRAINTS if c.label == "User"}
        assert user_props == {"id", "email"}

    def test_lens_has_id_and_name_constraints(self):
        lens_props = {c.property for c in UNIQUE_CONSTRAINTS if c.label == "Lens"}
        assert lens_props == {"id", "name"}


class TestIndexes:
    def test_poi_spatial_index_exists(self):
        poi_indexes = [i for i in INDEXES if i.label == "POI"]
        assert len(poi_indexes) == 1
        assert poi_indexes[0].index_type == "POINT"
        assert poi_indexes[0].properties == ("location",)

    def test_area_spatial_index_exists(self):
        area_indexes = [i for i in INDEXES if i.label == "Area"]
        assert len(area_indexes) == 1
        assert area_indexes[0].index_type == "POINT"
        assert area_indexes[0].properties == ("centroid",)

    def test_narrative_beat_composite_index(self):
        nb_indexes = [i for i in INDEXES if i.label == "NarrativeBeat"]
        assert len(nb_indexes) == 1
        assert nb_indexes[0].properties == ("active_status", "version")


class TestRelationships:
    def test_fourteen_relationship_types(self):
        """Schema_v3 §4 plus the family group (MEMBER_OF, DAY_OF): 14
        relationships."""
        assert len(RELATIONSHIP_TYPES) == 14

    def test_all_expected_types_present(self):
        expected = {
            "HAS_PROFILE",
            "IS_CAPTAIN_OF",
            "IS_CREW_OF",
            "PREFERS_LENS",
            "HAS_STOP",
            "ASSIGNED_TO",
            "AT_POI",
            "PLAYS_BEAT",
            "HAS_BEAT",
            "TAGGED_WITH",
            "IS_PARENT_OF",
            "WITHIN",
            "MEMBER_OF",
            "DAY_OF",
        }
        assert set(RELATIONSHIP_TYPES) == expected


class TestLenses:
    def test_eight_parent_lenses(self):
        """8 universal parent lenses — the genres that never change."""
        assert len(MVP_LENSES) == 8

    def test_twentyone_universal_child_lenses(self):
        """21 universal child lenses seeded globally."""
        assert len(DAG_CHILD_LENSES) == 21

    def test_twentyone_taggable_lenses(self):
        """21 universal taggable lenses (all children, no parents)."""
        assert len(TAGGABLE_LENSES) == 21

    def test_lens_names_are_unique(self):
        names = [lens["name"] for lens in MVP_LENSES]
        assert len(names) == len(set(names))

    def test_dag_child_references_existing_parent(self):
        parent_names = {lens["name"] for lens in MVP_LENSES}
        for child in DAG_CHILD_LENSES:
            assert child["parent_name"] in parent_names, (
                f"Child lens '{child['name']}' references unknown parent '{child['parent_name']}'"
            )

    def test_dag_child_parent_has_is_parent_true(self):
        parent_lookup = {l["name"]: l for l in MVP_LENSES}
        for child in DAG_CHILD_LENSES:
            parent = parent_lookup[child["parent_name"]]
            assert parent.get("is_parent") is True, (
                f"Parent '{child['parent_name']}' must have is_parent=True"
            )

    def test_no_parent_slug_in_taggable(self):
        parent_slugs = {l["name"] for l in MVP_LENSES if l.get("is_parent")}
        for slug in TAGGABLE_LENSES:
            assert slug not in parent_slugs, (
                f"Parent-only slug '{slug}' must not appear in TAGGABLE_LENSES"
            )

    def test_taggable_equals_all_children(self):
        """All parents have children now, so taggable = all children."""
        child_slugs = {c["name"] for c in DAG_CHILD_LENSES}
        assert set(TAGGABLE_LENSES) == child_slugs

    def test_all_parents_have_children(self):
        """Every parent lens must have at least one child."""
        parents_with_children = {c["parent_name"] for c in DAG_CHILD_LENSES}
        all_parents = {l["name"] for l in MVP_LENSES if l.get("is_parent")}
        assert all_parents == parents_with_children
