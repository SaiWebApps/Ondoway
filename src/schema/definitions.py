"""Schema definitions for the Ondoway Neo4j graph.

Pure data module — no database calls. Consumed by schema.constraints
and tests to keep the source of truth in one place.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UniqueConstraint:
    label: str
    property: str

    @property
    def name(self) -> str:
        return f"uniq_{self.label.lower()}_{self.property}"


@dataclass(frozen=True)
class Index:
    label: str
    properties: tuple[str, ...]
    index_type: str = "RANGE"  # RANGE | POINT

    @property
    def name(self) -> str:
        props = "_".join(self.properties)
        return f"idx_{self.label.lower()}_{props}"


# ---------------------------------------------------------------------------
# Constraints — Schema_v3 §3
# ---------------------------------------------------------------------------

UNIQUE_CONSTRAINTS: list[UniqueConstraint] = [
    UniqueConstraint("User", "id"),
    UniqueConstraint("User", "email"),
    UniqueConstraint("Profile", "id"),
    UniqueConstraint("Trip", "id"),
    UniqueConstraint("ItineraryItem", "id"),
    UniqueConstraint("POI", "id"),
    UniqueConstraint("NarrativeBeat", "id"),
    UniqueConstraint("Lens", "id"),
    UniqueConstraint("Lens", "name"),
    UniqueConstraint("Area", "id"),
    UniqueConstraint("Family", "id"),
]

# ---------------------------------------------------------------------------
# Indexes — Schema_v3 §3.5, §3.6
# ---------------------------------------------------------------------------

INDEXES: list[Index] = [
    Index("POI", ("location",), index_type="POINT"),
    Index("NarrativeBeat", ("active_status", "version"), index_type="RANGE"),
    Index("Area", ("centroid",), index_type="POINT"),
    Index("User", ("google_sub",)),
    Index("User", ("apple_sub",)),
]

# ---------------------------------------------------------------------------
# Relationship types — Schema_v3 §4
# ---------------------------------------------------------------------------

RELATIONSHIP_TYPES: list[str] = [
    "HAS_PROFILE",
    "IS_CAPTAIN_OF",
    "IS_CREW_OF",
    "MEMBER_OF",
    "PREFERS_LENS",
    "HAS_STOP",
    "ASSIGNED_TO",
    "AT_POI",
    "PLAYS_BEAT",
    "HAS_BEAT",
    "TAGGED_WITH",
    "IS_PARENT_OF",
    "WITHIN",
]


# ---------------------------------------------------------------------------
# Relationship property schemas — Schema_v3 §4.1
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelPropertyDef:
    """Declares a property that a relationship type supports."""

    name: str
    type: str  # "str", "int", "float", "bool"
    required: bool = False
    default: str | int | float | bool | None = None


RELATIONSHIP_SCHEMAS: dict[str, list[RelPropertyDef]] = {
    "HAS_PROFILE": [],
    "IS_CAPTAIN_OF": [],
    "IS_CREW_OF": [],
    "MEMBER_OF": [],
    "PREFERS_LENS": [
        RelPropertyDef("weight", "float", required=False, default=1.0),
    ],
    "HAS_STOP": [],
    "ASSIGNED_TO": [],
    "AT_POI": [],
    "PLAYS_BEAT": [],
    "HAS_BEAT": [
        RelPropertyDef("sort_order", "int", required=False, default=0),
    ],
    "TAGGED_WITH": [
        RelPropertyDef("confidence", "float", required=False, default=1.0),
    ],
    "IS_PARENT_OF": [],
    "WITHIN": [],
}

# ---------------------------------------------------------------------------
# Parent Lenses — 8 universal genres (never change)
# ---------------------------------------------------------------------------

MVP_LENSES: list[dict] = [
    {"name": "history", "display_label": "History", "is_parent": True},
    {"name": "arch_design", "display_label": "Architecture & Design", "is_parent": True},
    {"name": "arts_culture", "display_label": "Arts & Culture", "is_parent": True},
    {"name": "food_drink", "display_label": "Food & Drink", "is_parent": True},
    {"name": "stories_characters", "display_label": "Stories & Characters", "is_parent": True},
    {"name": "faith_spirituality", "display_label": "Faith & Spirituality", "is_parent": True},
    {"name": "nature_landscape", "display_label": "Nature & Landscape", "is_parent": True},
    {"name": "commerce_innovation", "display_label": "Commerce & Innovation", "is_parent": True},
]

# ---------------------------------------------------------------------------
# Universal Child Lenses — exist in every city (seeded globally)
# City-specific children are added by the lens-generate skill per city.
# ---------------------------------------------------------------------------

DAG_CHILD_LENSES: list[dict] = [
    # History children
    {"name": "hidden_history", "display_label": "Hidden History", "parent_name": "history"},
    {"name": "war_conflict", "display_label": "War & Conflict", "parent_name": "history"},
    {"name": "dark_history", "display_label": "Dark History", "parent_name": "history"},
    {"name": "social_change", "display_label": "Social Change", "parent_name": "history"},
    # Architecture & Design children
    {
        "name": "historic_arch",
        "display_label": "Historic Architecture",
        "parent_name": "arch_design",
    },
    {
        "name": "modern_design",
        "display_label": "Modern & Contemporary Design",
        "parent_name": "arch_design",
    },
    # Arts & Culture children
    {"name": "music_heritage", "display_label": "Music Heritage", "parent_name": "arts_culture"},
    {"name": "visual_art", "display_label": "Visual Art", "parent_name": "arts_culture"},
    {"name": "street_art", "display_label": "Street Art", "parent_name": "arts_culture"},
    {"name": "film_tv", "display_label": "Film & TV Locations", "parent_name": "arts_culture"},
    # Food & Drink children
    {"name": "historic_cuisine", "display_label": "Historic Cuisine", "parent_name": "food_drink"},
    {
        "name": "markets_street_food",
        "display_label": "Markets & Street Food",
        "parent_name": "food_drink",
    },
    # Stories & Characters children
    {
        "name": "local_legends",
        "display_label": "Local Legends & Folklore",
        "parent_name": "stories_characters",
    },
    {
        "name": "literary_heritage",
        "display_label": "Literary Heritage",
        "parent_name": "stories_characters",
    },
    {
        "name": "famous_residents",
        "display_label": "Famous Residents",
        "parent_name": "stories_characters",
    },
    # Faith & Spirituality children
    {
        "name": "historic_worship",
        "display_label": "Historic Houses of Worship",
        "parent_name": "faith_spirituality",
    },
    {
        "name": "sacred_traditions",
        "display_label": "Sacred Traditions",
        "parent_name": "faith_spirituality",
    },
    # Nature & Landscape children
    {
        "name": "parks_gardens",
        "display_label": "Parks & Gardens",
        "parent_name": "nature_landscape",
    },
    {
        "name": "waterways_views",
        "display_label": "Waterways & Views",
        "parent_name": "nature_landscape",
    },
    # Commerce & Innovation children
    {
        "name": "historic_markets",
        "display_label": "Historic Markets & Shopping",
        "parent_name": "commerce_innovation",
    },
    {
        "name": "science_tech",
        "display_label": "Science & Technology",
        "parent_name": "commerce_innovation",
    },
]

# ---------------------------------------------------------------------------
# Taggable lenses — only children are taggable, never parents.
# 21 universal children. City-specific children added at runtime.
# ---------------------------------------------------------------------------

TAGGABLE_LENSES: list[str] = [
    # History
    "hidden_history",
    "war_conflict",
    "dark_history",
    "social_change",
    # Architecture & Design
    "historic_arch",
    "modern_design",
    # Arts & Culture
    "music_heritage",
    "visual_art",
    "street_art",
    "film_tv",
    # Food & Drink
    "historic_cuisine",
    "markets_street_food",
    # Stories & Characters
    "local_legends",
    "literary_heritage",
    "famous_residents",
    # Faith & Spirituality
    "historic_worship",
    "sacred_traditions",
    # Nature & Landscape
    "parks_gardens",
    "waterways_views",
    # Commerce & Innovation
    "historic_markets",
    "science_tech",
]
