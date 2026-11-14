"""Integration tests for schema constraints and indexes."""

from src.schema.constraints import apply_all
from src.schema.definitions import INDEXES
from tests.conftest import needs_neo4j


@needs_neo4j
class TestConstraints:
    def test_apply_all_returns_counts(self, driver):
        result = apply_all(driver)
        assert result["constraints"] == 11
        assert result["indexes"] == 11

    def test_constraints_are_idempotent(self, driver):
        """Running apply_all twice should not raise."""
        apply_all(driver)
        result = apply_all(driver)
        assert result["constraints"] == 11

    def test_unique_constraints_exist_in_db(self, driver):
        apply_all(driver)
        with driver.session() as session:
            result = session.run("SHOW CONSTRAINTS YIELD name RETURN collect(name) AS names")
            names = result.single()["names"]
        assert "uniq_user_id" in names
        assert "uniq_user_email" in names
        assert "uniq_lens_name" in names

    def test_indexes_exist_in_db(self, driver):
        apply_all(driver)
        with driver.session() as session:
            result = session.run("SHOW INDEXES YIELD name RETURN collect(name) AS names")
            names = result.single()["names"]
        assert "idx_poi_location" in names
        assert "idx_narrativebeat_active_status_version" in names
        assert "idx_placerecipe_id" in names

    def test_every_lookup_key_is_indexed(self, driver):
        """Each defined index's schema is covered by an online index. A graph that
        already holds an equivalent index under another name, such as a uniqueness
        constraint's, keeps that one: IF NOT EXISTS creates no second copy."""
        apply_all(driver)
        with driver.session() as session:
            online = {
                (r["labels"][0], tuple(r["props"]))
                for r in session.run(
                    "SHOW INDEXES YIELD labelsOrTypes AS labels, properties AS props, state "
                    "WHERE labels IS NOT NULL AND state = 'ONLINE' RETURN labels, props"
                )
            }
        missing = [i.name for i in INDEXES if (i.label, i.properties) not in online]
        assert missing == []
