"""Unit tests for API models — no Neo4j required."""

import pytest
from pydantic import ValidationError

from src.api.models.nodes import (
    CREATE_MODELS,
    NarrativeBeatCreate,
    NodeLabel,
    NodeListResponse,
    NodeResponse,
    POICreate,
)

# ── NodeLabel enum ──


class TestNodeLabel:
    def test_has_nine_labels(self):
        assert len(NodeLabel) == 9

    def test_every_constrained_label_is_addressable(self):
        """The drift guard the family split exposed: a label that carries a
        unique constraint is a real node kind the API must be able to
        address — a constraint without an enum member makes its endpoints
        an unconditional 422."""
        from src.schema.definitions import UNIQUE_CONSTRAINTS

        constrained = {c.label for c in UNIQUE_CONSTRAINTS}
        addressable = {label.value for label in NodeLabel}
        assert constrained <= addressable, constrained - addressable

    def test_all_expected_labels_present(self):
        expected = {
            "User",
            "Profile",
            "Lens",
            "Trip",
            "ItineraryItem",
            "POI",
            "NarrativeBeat",
            "Family",
            "Area",
        }
        assert {label.value for label in NodeLabel} == expected

    def test_labels_are_string_values(self):
        for label in NodeLabel:
            assert isinstance(label.value, str)


# ── NodeResponse ──


class TestNodeResponse:
    def test_can_construct_with_required_fields(self):
        resp = NodeResponse(
            id="abc-123",
            labels=["User"],
            properties={"email": "test@example.com"},
        )
        assert resp.id == "abc-123"
        assert resp.labels == ["User"]
        assert resp.properties["email"] == "test@example.com"

    def test_properties_can_be_empty(self):
        resp = NodeResponse(id="x", labels=["Lens"], properties={})
        assert resp.properties == {}


# ── NodeListResponse ──


class TestNodeListResponse:
    def test_can_construct_empty_list(self):
        resp = NodeListResponse(items=[], total=0, skip=0, limit=50)
        assert resp.items == []
        assert resp.total == 0

    def test_can_construct_with_items(self):
        item = NodeResponse(id="1", labels=["User"], properties={"email": "a@b.com"})
        resp = NodeListResponse(items=[item], total=1, skip=0, limit=50)
        assert len(resp.items) == 1
        assert resp.items[0].id == "1"


# ── CREATE_MODELS mapping ──


class TestCreateModels:
    def test_every_label_has_a_create_model(self):
        for label in NodeLabel:
            assert label in CREATE_MODELS, f"Missing create model for {label.value}"

    def test_user_create_requires_email(self):
        model = CREATE_MODELS[NodeLabel.User]
        instance = model(email="test@example.com")
        assert instance.email == "test@example.com"

    def test_poi_create_requires_lat_lng(self):
        model = CREATE_MODELS[NodeLabel.POI]
        instance = model(
            name="Test POI", city_name="paris", latitude=48.8, longitude=2.3, importance_tier=3
        )
        assert instance.latitude == 48.8
        assert instance.longitude == 2.3

    def test_lens_create_requires_name_and_label(self):
        model = CREATE_MODELS[NodeLabel.Lens]
        instance = model(name="test_lens", display_label="Test Lens")
        assert instance.name == "test_lens"
        assert instance.display_label == "Test Lens"


# ── Beat enrichment fields ──


class TestBeatEnrichmentFields:
    def test_beat_create_with_enrichment_fields(self):
        beat = NarrativeBeatCreate(
            script_body="The cathedral was built in 1163.",
            entities=["Notre-Dame"],
            sensory_anchor=True,
            duration_sec=10,
            narrative_function="hook",
            beat_type="anecdote",
            emotional_register="dramatic",
        )
        d = beat.model_dump()
        assert d["entities"] == ["Notre-Dame"]
        assert d["sensory_anchor"] is True
        assert d["duration_sec"] == 10
        assert d["narrative_function"] == "hook"
        assert d["beat_type"] == "anecdote"
        assert d["emotional_register"] == "dramatic"

    def test_beat_create_without_enrichment_fields(self):
        beat = NarrativeBeatCreate(script_body="A simple beat.")
        d = beat.model_dump()
        assert d["entities"] == []
        assert d["sensory_anchor"] is None
        assert d["narrative_function"] == ""
        assert d["beat_type"] == ""
        assert d["emotional_register"] == ""


# ── POI role field ──


class TestPOIRoleField:
    def test_poi_create_with_poi_role(self):
        poi = POICreate(
            name="Île de la Cité",
            city_name="paris",
            latitude=48.8534,
            longitude=2.3488,
            importance_tier=5,
            poi_role="setting",
        )
        assert poi.model_dump()["poi_role"] == "setting"

    def test_poi_create_default_poi_role(self):
        poi = POICreate(
            name="Test POI",
            city_name="paris",
            latitude=48.8,
            longitude=2.3,
            importance_tier=3,
        )
        assert poi.model_dump()["poi_role"] == "stop"


# ── POI coordinate validation ──


class TestPOICoordinateValidation:
    """Test POICreate field_validators for latitude and longitude ranges."""

    def _make_poi(self, *, latitude: float = 48.8, longitude: float = 2.3) -> POICreate:
        return POICreate(
            name="Test",
            city_name="paris",
            latitude=latitude,
            longitude=longitude,
            importance_tier=3,
        )

    def test_latitude_above_90_rejected(self):
        with pytest.raises(ValidationError, match="latitude must be between"):
            self._make_poi(latitude=90.1)

    def test_latitude_below_neg90_rejected(self):
        with pytest.raises(ValidationError, match="latitude must be between"):
            self._make_poi(latitude=-90.1)

    def test_longitude_above_180_rejected(self):
        with pytest.raises(ValidationError, match="longitude must be between"):
            self._make_poi(longitude=180.1)

    def test_longitude_below_neg180_rejected(self):
        with pytest.raises(ValidationError, match="longitude must be between"):
            self._make_poi(longitude=-180.1)

    def test_boundary_latitude_90_accepted(self):
        poi = self._make_poi(latitude=90.0)
        assert poi.latitude == 90.0

    def test_boundary_latitude_neg90_accepted(self):
        poi = self._make_poi(latitude=-90.0)
        assert poi.latitude == -90.0

    def test_boundary_longitude_180_accepted(self):
        poi = self._make_poi(longitude=180.0)
        assert poi.longitude == 180.0

    def test_boundary_longitude_neg180_accepted(self):
        poi = self._make_poi(longitude=-180.0)
        assert poi.longitude == -180.0
