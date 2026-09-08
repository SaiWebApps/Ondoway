"""Pydantic models for node CRUD operations."""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _normalized_script_body_hash(script_body: str) -> str:
    """SHA-256 of `re.sub(r'\\s+', ' ', body.lower().strip())`.

    Single source of truth for hash computation; reused by validators,
    migration, and dedup tooling so a hash mismatch always means the body
    actually differs after normalization, not because two callers normalized
    differently.
    """
    normalized = re.sub(r"\s+", " ", script_body.lower().strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def canonical_name_key(name: str) -> str:
    """Derive the canonical dedup key for a POI `name` (defect 3).

    Collapses internal whitespace runs, trims, and lowercases so casing and
    whitespace variants ('Notre-Dame', 'notre-dame', 'Notre-Dame ') map to a
    single MERGE key while `n.name` retains its display casing. Mirrors the
    same normalization applied to `city_name` at the POI ingestion choke point.
    """
    return re.sub(r"\s+", " ", name.strip()).lower()


# Properties that identify a node/edge or form its MERGE key must never be
# rewritten by a partial update — doing so orphans the node from every
# id-keyed query/edge (id/created_at) or forks the idempotent MERGE key
# (name/name_key/city_name/area_type/script_body). update_node/update_edge
# reject any update touching these (defect 1).
_UNIVERSAL_PROTECTED_KEYS: frozenset[str] = frozenset({"id", "created_at"})

_LABEL_MERGE_KEY_FIELDS: dict[str, frozenset[str]] = {
    # POI MERGE key is (name_key, city_name); `name` is the display source of
    # name_key so it is protected too (defect 3 canonicalization).
    "POI": frozenset({"name", "name_key", "city_name"}),
    # Area MERGE key is (name, area_type, city_name).
    "Area": frozenset({"name", "area_type", "city_name"}),
    # NarrativeBeat is created by id (defect 4); id is already universal.
    "NarrativeBeat": frozenset(),
}


def protected_node_keys(label: str) -> frozenset[str]:
    """Return the set of property names that update_node must refuse for `label`."""
    return _UNIVERSAL_PROTECTED_KEYS | _LABEL_MERGE_KEY_FIELDS.get(label, frozenset())


# Edge identity: only r.id / r.created_at are protected (MERGE keys for edges
# are the endpoint nodes, not stored properties).
def protected_edge_keys() -> frozenset[str]:
    """Return the set of property names that update_edge must refuse."""
    return _UNIVERSAL_PROTECTED_KEYS


class NodeLabel(StrEnum):
    """Valid node labels from Schema_v3."""

    User = "User"
    Profile = "Profile"
    Lens = "Lens"
    Trip = "Trip"
    ItineraryItem = "ItineraryItem"
    POI = "POI"
    NarrativeBeat = "NarrativeBeat"
    Area = "Area"
    Family = "Family"


class NodeResponse(BaseModel):
    """Returned from all node endpoints."""

    id: str
    labels: list[str]
    properties: dict[str, Any]


class NodeListResponse(BaseModel):
    """Paginated list of nodes."""

    items: list[NodeResponse]
    total: int
    skip: int
    limit: int


# ── Per-label CREATE models ──


class UserCreate(BaseModel):
    email: str = Field(max_length=320)


class ProfileCreate(BaseModel):
    display_name: str = Field(max_length=500)


class LensCreate(BaseModel):
    name: str = Field(max_length=500)
    display_label: str = Field(max_length=500)


class FamilyCreate(BaseModel):
    """A persistent group of profiles; the name is the group's own choice."""

    name: str = Field(default="", max_length=500)


class TripCreate(BaseModel):
    name: str = Field(max_length=500)
    start_date: str = Field(max_length=50)
    end_date: str = Field(max_length=50)
    cover_image_url: str = Field(default="", max_length=2000)
    status: str = Field(default="planning", max_length=50)


class ItineraryItemCreate(BaseModel):
    sort_order: int
    scheduled_date: str = Field(max_length=50)
    start_time: str = Field(max_length=50)
    duration_min: int


POIRole = Literal["stop", "setting", "walk_by_only"]


class POIAnchor(BaseModel):
    """One reviewed anchor of a marquee place (Phase 7 S7.7 B) as the export chunks
    carry it — the API-side twin of `src.tour.contract.Anchor`, declared here because
    POICreate ignores undeclared fields (see the capacity trio below). Stored
    JSON-encoded by `_encode_complex_props` (a list of dicts), read back by the corpus
    loader."""

    label: str
    sub_locations: list[str]
    lat: float
    lng: float
    radius_m: float
    indoor: bool = False
    basis: str = ""


class POICreate(BaseModel):
    name: str = Field(max_length=500)
    city_name: str  # REQUIRED — matches AreaCreate.city_name convention.
    # Part of the (name, city_name) MERGE key for multi-city safety.
    short_description: str = Field(default="", max_length=2000)
    latitude: float
    longitude: float
    importance_tier: int  # REQUIRED — no default. Silent default of 1 caused
    # famous landmarks to be demoted in earlier pipeline runs.
    # See tests/test_export_consistency.py for the regression guard.
    trigger_radius: int = 10
    # How long a visitor usefully spends AT this place, in two numbers.
    # `typical_duration_min` is MINUTES spent WITHOUT going in; it is the only
    # number a street, bridge or square ever gets. `visit_seconds_inside` is
    # SECONDS spent INSIDE, and is None for anything with no interior. Two are
    # needed because one cannot serve two visitors: Camille gives Sainte-Chapelle
    # 66 minutes including the queue, Theo gives the same building 15 from the
    # outside (docs/personas/01-architecture-pilgrim.md, 02-dark-history-walker.md).
    # POICreate has no model_config, so pydantic's default extra="ignore" applies:
    # any field NOT declared here is silently dropped from a POST /api/nodes/POI
    # body. These three are declared for exactly that reason.
    typical_duration_min: int = 30
    visit_seconds_inside: int | None = None
    # One sentence a person who has never visited the city can judge the numbers
    # by ("interior, ~30 rooms, typical visit 45-60 min"). Not provenance and not
    # source text, so unlike `source_passage` it IS forwarded to the graph — it is
    # the only audit trail a generated capacity has.
    visit_basis: str = Field(default="", max_length=500)
    kid_friendly: str = Field(default="yes", max_length=50)
    name_variations: list[str] = []
    poi_role: POIRole = "stop"
    # The reviewed anchors (Phase 7 S7.7 B); null/absent = none, the safe default.
    anchors: list[POIAnchor] | None = None
    parent_poi: str | None = None  # Set on sub-POIs; names the parent POI
    source_passage: str = ""  # Required when parent_poi is set (AC-9):
    # verbatim/near-verbatim quote from source
    # text grounding the poi_role classification
    establishing_not_applicable: bool = False  # Per AC-6: auto-flag set during
    # extraction when a poi_role: stop
    # POI has tier ≤ 2 and lacks an
    # establishing beat. Higher-tier
    # stops must carry a real beat.
    force_create: bool = False

    @field_validator("importance_tier")
    @classmethod
    def importance_tier_in_range(cls, v: int) -> int:
        if not 1 <= v <= 5:
            raise ValueError("importance_tier must be between 1 and 5")
        return v

    @field_validator("latitude")
    @classmethod
    def latitude_in_range(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError("latitude must be between -90 and 90")
        return v

    @field_validator("longitude")
    @classmethod
    def longitude_in_range(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("longitude must be between -180 and 180")
        return v

    @model_validator(mode="after")
    def sub_poi_requires_source_passage(self) -> POICreate:
        """Per AC-9: any POI with parent_poi set must carry a non-empty
        source_passage quoting the source text that grounds the poi_role."""
        if self.parent_poi and not self.source_passage.strip():
            raise ValueError(
                "sub-POI (parent_poi is set) requires a non-empty source_passage "
                "grounding its poi_role classification"
            )
        return self


Direction = Literal["up", "down", "north", "south", "east", "west", "here"]
FeatureType = Literal[
    "architectural_detail",
    "plaque",
    "view",
    "interior",
    "adjacent_landmark",
]


class PhysicalCue(BaseModel):
    """One directional/spatial pointer tied to a beat.

    Paired with a beat that has sensory_anchor=true. The tour builder uses
    direction to orient the listener (cardinal translates to relative at
    runtime via phone compass) and feature_type to pace the stop (a plaque
    needs reading time; a view needs standing time).
    """

    cue: str
    direction: Direction
    feature_type: FeatureType


class InlineForeignPhrase(BaseModel):
    """Structured companion to inline foreign-phrase preservation (B3).

    When a source text contains `foreign-word (gloss)` or `foreign-word, meaning X`,
    the extractor keeps both the word and gloss verbatim in `script_body` AND
    records the pair here so downstream skills (TTS pronunciation, glossary
    rendering, cross-beat theme discovery) can reason over the foreign
    vocabulary surface area without re-parsing prose.
    """

    phrase: str
    gloss: str


class NarrativeBeatCreate(BaseModel):
    script_body: str = Field(max_length=10000)
    version: int = 1
    active_status: str = Field(default="active", max_length=50)
    audio_url: str = Field(default="", max_length=2000)
    duration_sec: int = 60
    kid_friendly: str = Field(default="yes", max_length=50)
    entities: list[str] = []
    sensory_anchor: bool | None = None
    narrative_function: str = ""
    beat_type: str = ""
    emotional_register: str = ""
    subject_tag: str = ""
    physical_cues: list[PhysicalCue] = []
    script_body_hash: str = ""
    book_slug: str = ""
    topic_slug: str = ""
    city_name: str = ""
    source_chunk_slug: str = ""

    # Structural enrichment fields added by unified_v2 extraction prompt.
    # All optional — legacy beats have them as None/""/[] and upload without issue.
    sub_location: str | None = None  # Within-POI spatial tag (façade, crypt, nave...)
    trigger_address: str | None = None  # Address-level GPS trigger ("no. 6 place des Vosges")
    beat_length_class: str = ""  # anchor | mid | seasoning | micro; empty on legacy
    inline_foreign_phrases: list[InlineForeignPhrase] = []
    pronunciation: str | None = None  # Phonetic approximation, e.g. "plass-day-voge"

    @field_validator("subject_tag")
    @classmethod
    def subject_tag_shape(cls, v: str) -> str:
        if v == "":
            return v  # optional until Scope 4 re-extraction; empty allowed on legacy beats
        if not 1 <= len(v) <= 32:
            raise ValueError("subject_tag must be between 1 and 32 characters")
        words = v.split()
        if not 1 <= len(words) <= 3:
            raise ValueError("subject_tag must be 1-3 words")
        return v

    @model_validator(mode="after")
    def hash_matches_normalized_body(self) -> NarrativeBeatCreate:
        expected = _normalized_script_body_hash(self.script_body)
        if not self.script_body_hash:
            self.script_body_hash = expected
        elif self.script_body_hash != expected:
            raise ValueError(
                "script_body_hash does not match SHA-256 of normalized "
                "script_body. Recompute or omit to auto-fill."
            )
        return self


class AreaCreate(BaseModel):
    name: str
    area_type: Literal["city", "district", "neighborhood", "island", "corridor"]
    city_name: str
    boundary: str  # WKT POLYGON string
    centroid_lat: float
    centroid_lng: float
    short_description: str = ""

    @field_validator("centroid_lat")
    @classmethod
    def centroid_lat_in_range(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError("centroid_lat must be between -90 and 90")
        return v

    @field_validator("centroid_lng")
    @classmethod
    def centroid_lng_in_range(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("centroid_lng must be between -180 and 180")
        return v

    @field_validator("boundary")
    @classmethod
    def boundary_is_wkt_polygon(cls, v: str) -> str:
        if not v.startswith("POLYGON((") or not v.endswith("))"):
            raise ValueError(
                "boundary must be a WKT POLYGON string "
                "starting with 'POLYGON((' and ending with '))'"
            )
        return v


CREATE_MODELS: dict[NodeLabel, type[BaseModel]] = {
    NodeLabel.User: UserCreate,
    NodeLabel.Profile: ProfileCreate,
    NodeLabel.Lens: LensCreate,
    NodeLabel.Trip: TripCreate,
    NodeLabel.ItineraryItem: ItineraryItemCreate,
    NodeLabel.POI: POICreate,
    NodeLabel.NarrativeBeat: NarrativeBeatCreate,
    NodeLabel.Area: AreaCreate,
    NodeLabel.Family: FamilyCreate,
}


# ── UPDATE model ──


class NodeUpdate(BaseModel):
    """Partial update — only provided fields are SET."""

    properties: dict[str, Any]
