"""Integration tests for the core traversal patterns that src/verify/traversals.py runs."""

import pytest

from src.schema.constraints import apply_all
from src.seed.runner import seed_all
from src.verify.traversals import (
    run_all_traversals,
    run_dag_traversal,
    run_planner_traversal,
    run_wanderer_traversal,
)
from tests.conftest import needs_neo4j


@needs_neo4j
class TestTraversals:
    @pytest.fixture(autouse=True)
    def _setup(self, driver):
        with driver.session() as s:
            s.run("MATCH (n) DETACH DELETE n")
        apply_all(driver)
        seed_all(driver)

    def test_planner_returns_at_least_three_rows(self, driver):
        result = run_planner_traversal(driver)
        assert result.passed
        assert result.row_count >= 3

    def test_planner_rows_contain_expected_fields(self, driver):
        result = run_planner_traversal(driver)
        assert len(result.sample_rows) > 0
        row = result.sample_rows[0]
        assert "trip" in row
        assert "poi" in row
        assert "lens" in row

    def test_wanderer_returns_at_least_one_row(self, driver):
        result = run_wanderer_traversal(driver)
        assert result.passed
        assert result.row_count >= 1

    def test_wanderer_matches_mom_lens_preferences(self, driver):
        result = run_wanderer_traversal(driver)
        lenses = {row["lens"] for row in result.sample_rows}
        mom_lenses = {"Hidden History", "Historic Cuisine", "Literary Heritage"}
        assert lenses.issubset(mom_lenses), f"Unexpected lenses: {lenses - mom_lenses}"

    def test_dag_returns_at_least_one_row(self, driver):
        result = run_dag_traversal(driver)
        assert result.passed
        assert result.row_count >= 1

    def test_dag_has_architecture_parent(self, driver):
        result = run_dag_traversal(driver)
        parents = {row["parent"] for row in result.sample_rows}
        assert "Architecture & Design" in parents

    def test_all_traversals_pass(self, driver):
        results = run_all_traversals(driver)
        assert all(r.passed for r in results), (
            f"Failed: {[r.name for r in results if not r.passed]}"
        )
