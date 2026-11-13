"""Execute and validate the three core traversal patterns the graph must answer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from neo4j import Driver

from src.connection import get_database


@dataclass(frozen=True)
class TraversalResult:
    name: str
    row_count: int
    min_expected: int
    sample_rows: list[dict]

    @property
    def passed(self) -> bool:
        return self.row_count >= self.min_expected


# ---------------------------------------------------------------------------
# Planner: Trip → ItineraryItem → POI → NarrativeBeat → Lens
# ---------------------------------------------------------------------------

_PLANNER_QUERY = """
MATCH (t:Trip)-[:HAS_STOP]->(i:ItineraryItem)-[:AT_POI]->(p:POI)
      -[:HAS_BEAT]->(b:NarrativeBeat)-[:TAGGED_WITH]->(l:Lens)
RETURN t.name AS trip, p.name AS poi, l.display_label AS lens,
       b.duration_sec AS duration
ORDER BY i.sort_order
"""


def run_planner_traversal(driver: Driver) -> TraversalResult:
    """Structured Discovery: a trip's stops, their POIs, beats and lenses."""
    with driver.session(database=get_database()) as session:
        records = list(session.run(_PLANNER_QUERY))
    rows = [dict(r) for r in records]
    return TraversalResult(name="Planner", row_count=len(rows), min_expected=3, sample_rows=rows)


# ---------------------------------------------------------------------------
# Wanderer: Profile → Lens ← Beat ← POI (with spatial filter)
# ---------------------------------------------------------------------------

_WANDERER_QUERY = """
MATCH (pr:Profile)-[:PREFERS_LENS]->(l:Lens)<-[:TAGGED_WITH]-(b:NarrativeBeat)
      <-[:HAS_BEAT]-(p:POI)
WHERE pr.display_name = 'Mom'
  AND point.distance(p.location, point({latitude: 48.8566, longitude: 2.3522, srid: 4326})) < 5000
RETURN pr.display_name AS profile, l.display_label AS lens,
       p.name AS poi, b.duration_sec AS duration
"""


def run_wanderer_traversal(driver: Driver) -> TraversalResult:
    """Spontaneous Exploration: beats near a point on a profile's preferred lenses."""
    with driver.session(database=get_database()) as session:
        records = list(session.run(_WANDERER_QUERY))
    rows = [dict(r) for r in records]
    return TraversalResult(name="Wanderer", row_count=len(rows), min_expected=1, sample_rows=rows)


# ---------------------------------------------------------------------------
# DAG: Lens → Lens (IS_PARENT_OF)
# ---------------------------------------------------------------------------

_DAG_QUERY = """
MATCH (parent:Lens)-[:IS_PARENT_OF]->(child:Lens)
RETURN parent.display_label AS parent, child.display_label AS child
"""


def run_dag_traversal(driver: Driver) -> TraversalResult:
    """Verify IS_PARENT_OF hierarchy works."""
    with driver.session(database=get_database()) as session:
        records = list(session.run(_DAG_QUERY))
    rows = [dict(r) for r in records]
    return TraversalResult(name="DAG", row_count=len(rows), min_expected=1, sample_rows=rows)


def run_all_traversals(driver: Driver) -> list[TraversalResult]:
    """Execute all traversal checks and return results."""
    return [
        run_planner_traversal(driver),
        run_wanderer_traversal(driver),
        run_dag_traversal(driver),
    ]
