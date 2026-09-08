"""Integration tests for schema constraints and indexes."""

from src.schema.constraints import apply_all
from tests.conftest import needs_neo4j


@needs_neo4j
class TestConstraints:
    def test_apply_all_returns_counts(self, driver):
        result = apply_all(driver)
        assert result["constraints"] == 11
        assert result["indexes"] == 5

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
