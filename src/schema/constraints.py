"""Create Neo4j constraints and indexes defined in definitions.py.

Uses IF NOT EXISTS so every call is idempotent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.connection import get_database
from src.schema.definitions import INDEXES, UNIQUE_CONSTRAINTS, Index, UniqueConstraint

if TYPE_CHECKING:
    from neo4j import Driver


def _create_unique_constraint(tx, constraint: UniqueConstraint) -> None:
    tx.run(
        f"CREATE CONSTRAINT {constraint.name} IF NOT EXISTS "
        f"FOR (n:{constraint.label}) REQUIRE n.{constraint.property} IS UNIQUE"
    )


def _create_index(tx, index: Index) -> None:
    props = ", ".join(f"n.{p}" for p in index.properties)
    index_kind = "POINT" if index.index_type == "POINT" else "RANGE"
    tx.run(
        f"CREATE {index_kind} INDEX {index.name} IF NOT EXISTS FOR (n:{index.label}) ON ({props})"
    )


def apply_constraints(driver: Driver) -> int:
    """Create all unique constraints. Returns count created."""
    with driver.session(database=get_database()) as session:
        for constraint in UNIQUE_CONSTRAINTS:
            session.execute_write(_create_unique_constraint, constraint)
    return len(UNIQUE_CONSTRAINTS)


def apply_indexes(driver: Driver) -> int:
    """Create all indexes and wait until every one is online. Returns count created.

    A new index populates in the background; a query planned before it is online
    scans the whole label instead, so callers only get the index once this returns.
    """
    with driver.session(database=get_database()) as session:
        for index in INDEXES:
            session.execute_write(_create_index, index)
        session.run("CALL db.awaitIndexes(600)").consume()
    return len(INDEXES)


def apply_all(driver: Driver) -> dict[str, int]:
    """Apply all constraints and indexes. Returns summary counts."""
    return {
        "constraints": apply_constraints(driver),
        "indexes": apply_indexes(driver),
    }
