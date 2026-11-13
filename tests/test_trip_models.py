"""Unit tests for trip generation Pydantic models — no Neo4j required."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.models.trips import (
    GeneratedStop,
    TripGenerateRequest,
    TripGenerateResponse,
    TripPreviewResponse,
    TripPreviewTourability,
)
from src.tour.contract import RouteOption, RouteOptionStop


class TestTripGenerateRequest:
    """Validate TripGenerateRequest model constraints."""

    # POST /api/v1/trips/generate returns 201
    def test_trip_generate_request_valid_minimal(self):
        """Minimal valid request with only required fields."""
        req = TripGenerateRequest(
            profile_id="prof-123",
            center_lat=48.858,
            center_lng=2.294,
            start_date="2026-05-01",
            end_date="2026-05-03",
        )
        assert req.profile_id == "prof-123"
        assert req.center_lat == 48.858
        assert req.center_lng == 2.294
        assert req.start_date == "2026-05-01"

    # POST /api/v1/trips/generate returns 201
    def test_trip_generate_request_valid_all_fields(self):
        """Request with all optional fields explicitly set."""
        req = TripGenerateRequest(
            profile_id="prof-456",
            center_lat=48.854,
            center_lng=2.332,
            radius_m=5000,
            max_stops=15,
            duration_min=120,
            start_date="2026-06-01",
            end_date="2026-06-05",
            start_time="10:30",
            kid_friendly_only=True,
            trip_name="My Paris Trip",
        )
        assert req.radius_m == 5000
        assert req.max_stops == 15
        assert req.duration_min == 120
        assert req.start_time == "10:30"
        assert req.kid_friendly_only is True
        assert req.trip_name == "My Paris Trip"

    # POST /api/v1/trips/generate returns 201
    def test_trip_generate_request_lat_out_of_range(self):
        """Latitude > 90 must be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TripGenerateRequest(
                profile_id="p",
                center_lat=91.0,
                center_lng=2.0,
                start_date="2026-05-01",
                end_date="2026-05-03",
            )
        assert "center_lat" in str(exc_info.value)

    # POST /api/v1/trips/generate returns 201
    def test_trip_generate_request_lng_out_of_range(self):
        """Longitude > 180 must be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TripGenerateRequest(
                profile_id="p",
                center_lat=48.0,
                center_lng=181.0,
                start_date="2026-05-01",
                end_date="2026-05-03",
            )
        assert "center_lng" in str(exc_info.value)

    # POST /api/v1/trips/generate returns 201
    def test_trip_generate_request_radius_too_large(self):
        """radius_m > 10000 must be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TripGenerateRequest(
                profile_id="p",
                center_lat=48.0,
                center_lng=2.0,
                radius_m=10001,
                start_date="2026-05-01",
                end_date="2026-05-03",
            )
        assert "radius_m" in str(exc_info.value)

    def test_trip_generate_request_duration_bounds(self):
        """duration_min must satisfy the engine's TourInput bounds (1-600) at
        request validation, so out-of-range values 422 instead of 500."""
        for bad in (0, -5, 601):
            with pytest.raises(ValidationError) as exc_info:
                TripGenerateRequest(
                    profile_id="p",
                    center_lat=48.0,
                    center_lng=2.0,
                    duration_min=bad,
                    start_date="2026-05-01",
                    end_date="2026-05-03",
                )
            assert "duration_min" in str(exc_info.value)

    # POST /api/v1/trips/generate returns 201
    def test_trip_generate_request_max_stops_capped(self):
        """max_stops > 30 must be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TripGenerateRequest(
                profile_id="p",
                center_lat=48.0,
                center_lng=2.0,
                max_stops=31,
                start_date="2026-05-01",
                end_date="2026-05-03",
            )
        assert "max_stops" in str(exc_info.value)

    # POST /api/v1/trips/generate returns 201
    def test_trip_generate_request_defaults(self):
        """Defaults are applied correctly when optional fields omitted."""
        req = TripGenerateRequest(
            profile_id="p",
            center_lat=0.0,
            center_lng=0.0,
            start_date="2026-01-01",
            end_date="2026-01-02",
        )
        assert req.radius_m == 3000
        assert req.max_stops == 10
        assert req.duration_min is None
        assert req.start_time == "09:00"
        assert req.lenses is None
        assert req.round_trip is False

    def test_trip_generate_request_lenses_and_round_trip(self):
        """lenses normalize (blanks dropped) and round_trip is accepted."""
        req = TripGenerateRequest(
            profile_id="p",
            center_lat=48.0,
            center_lng=2.0,
            start_date="2026-01-01",
            end_date="2026-01-02",
            lenses=[" dark_history ", "", "  ", "literary_heritage"],
            round_trip=True,
        )
        assert req.lenses == ["dark_history", "literary_heritage"]
        assert req.round_trip is True

    def test_trip_generate_request_empty_lenses_become_none(self):
        """An all-blank lens list normalizes to None (falls back to profile/default)."""
        req = TripGenerateRequest(
            profile_id="p",
            center_lat=48.0,
            center_lng=2.0,
            start_date="2026-01-01",
            end_date="2026-01-02",
            lenses=["", "   "],
        )
        assert req.lenses is None
        assert req.kid_friendly_only is False
        assert req.trip_name is None


class TestGeneratedStop:
    def test_a_rest_stop_has_no_beat_and_still_rides_the_wire(self):
        """A rest — a bench the party or the "more breaks" dial seated — has no story
        beat, so ``beat_id`` is None (rests are promise-grade stops of
        the day, not narration). Until 2026-08-18 the field was required and every
        day that seated a rest 500'd at generate on the app path; the class of that
        error is a contract written for story stops applied to a rest. UNDO: make
        beat_id required again -> RED."""
        bench = GeneratedStop(
            sort_order=2,
            poi_id="bench-1",
            poi_name="Bench",
            lat=48.86,
            lng=2.33,
            beat_id=None,
            duration_min=8,
            importance_tier=1,
            start_time="14:20",
            dwell_seconds=480,
        )
        assert bench.beat_id is None and bench.beat_ids == []
        assert GeneratedStop.model_validate(bench.model_dump()).beat_id is None

    """Validate GeneratedStop model."""

    # Each stop has required fields
    def test_generated_stop_model(self):
        """GeneratedStop can be constructed with all required fields."""
        stop = GeneratedStop(
            sort_order=1,
            poi_id="poi-abc",
            poi_name="Eiffel Tower",
            lat=48.858,
            lng=2.294,
            beat_id="beat-xyz",
            lens_name="hidden_history",
            lens_display="Hidden History",
            duration_min=5,
            importance_tier=5,
            start_time="09:00",
        )
        assert stop.sort_order == 1
        assert stop.poi_id == "poi-abc"
        assert stop.poi_name == "Eiffel Tower"
        assert stop.lat == 48.858
        assert stop.lng == 2.294
        assert stop.beat_id == "beat-xyz"
        assert stop.lens_name == "hidden_history"
        assert stop.lens_display == "Hidden History"
        assert stop.duration_min == 5
        assert stop.importance_tier == 5
        assert stop.start_time == "09:00"
        # New optional fields default to None
        assert stop.script_body is None
        assert stop.audio_url is None
        assert stop.audio_duration_sec is None
        # Additive fields default empty
        assert stop.beat_ids == []
        assert stop.dwell_seconds == 0

    def test_generated_stop_lens_fields_optional(self):
        """lens_name/lens_display default None — a stop may have no lensed beat."""
        stop = GeneratedStop(
            sort_order=1,
            poi_id="poi-abc",
            poi_name="Plaque",
            lat=48.85,
            lng=2.35,
            beat_id="b1",
            duration_min=2,
            importance_tier=2,
            start_time="09:00",
        )
        assert stop.lens_name is None
        assert stop.lens_display is None

    def test_generated_stop_transit_polyline_optional(self):
        """transit_polyline defaults None; carries the routed leg shape."""
        base = dict(
            sort_order=1,
            poi_id="poi-abc",
            poi_name="Louvre",
            lat=48.86,
            lng=2.34,
            beat_id="b1",
            duration_min=10,
            importance_tier=5,
            start_time="09:00",
        )
        assert GeneratedStop(**base).transit_polyline is None
        stop = GeneratedStop(**base, transit_polyline="encoded6_abc")
        assert stop.transit_polyline == "encoded6_abc"

    def test_generated_stop_multi_beat_fields(self):
        """GeneratedStop carries all beat_ids + dwell_seconds from the engine."""
        stop = GeneratedStop(
            sort_order=1,
            poi_id="poi-abc",
            poi_name="Louvre",
            lat=48.86,
            lng=2.34,
            beat_id="b1",
            lens_name="art",
            lens_display="Art",
            duration_min=10,
            importance_tier=5,
            start_time="09:00",
            beat_ids=["b1", "b2", "b3"],
            dwell_seconds=600,
        )
        assert stop.beat_ids == ["b1", "b2", "b3"]
        assert stop.dwell_seconds == 600

    def test_generated_stop_with_audio_fields(self):
        """GeneratedStop accepts optional script_body, audio_url, audio_duration_sec."""
        stop = GeneratedStop(
            sort_order=2,
            poi_id="poi-def",
            poi_name="Notre-Dame",
            lat=48.853,
            lng=2.349,
            beat_id="beat-456",
            lens_name="architecture",
            lens_display="Architecture",
            duration_min=10,
            importance_tier=5,
            start_time="10:30",
            script_body="The flying buttresses of Notre-Dame...",
            audio_url="https://cdn.ondoway.com/beats/notre_dame/beat-456.mp3",
            audio_duration_sec=185.5,
        )
        assert stop.script_body == "The flying buttresses of Notre-Dame..."
        assert stop.audio_url == "https://cdn.ondoway.com/beats/notre_dame/beat-456.mp3"
        assert stop.audio_duration_sec == 185.5

    def test_generated_stop_serializes_null_audio_fields(self):
        """JSON serialization includes null audio fields when not provided."""
        stop = GeneratedStop(
            sort_order=1,
            poi_id="poi-1",
            poi_name="Test",
            lat=48.0,
            lng=2.0,
            beat_id="b-1",
            lens_name="history",
            lens_display="History",
            duration_min=5,
            importance_tier=3,
            start_time="09:00",
        )
        data = stop.model_dump()
        assert "script_body" in data
        assert "audio_url" in data
        assert "audio_duration_sec" in data
        assert data["script_body"] is None
        assert data["audio_url"] is None
        assert data["audio_duration_sec"] is None


class TestTripGenerateResponse:
    """Validate TripGenerateResponse model."""

    # Response has trip_id, trip_name, profile_id, etc.
    def test_trip_generate_response_model(self):
        """TripGenerateResponse can be constructed with all required fields."""
        stop = GeneratedStop(
            sort_order=1,
            poi_id="poi-1",
            poi_name="Test POI",
            lat=48.0,
            lng=2.0,
            beat_id="beat-1",
            lens_name="history",
            lens_display="History",
            duration_min=3,
            importance_tier=5,
            start_time="09:00",
        )
        resp = TripGenerateResponse(
            trip_id="trip-001",
            trip_name="My Trip",
            profile_id="prof-1",
            total_stops=1,
            total_duration_min=3,
            anchor_count=1,
            flavour_count=0,
            stops=[stop],
        )
        assert resp.trip_id == "trip-001"
        assert resp.trip_name == "My Trip"
        assert resp.profile_id == "prof-1"
        assert resp.total_stops == 1
        assert resp.total_duration_min == 3
        assert resp.anchor_count == 1
        assert resp.flavour_count == 0
        assert len(resp.stops) == 1
        assert resp.stops[0].poi_id == "poi-1"
        # Additive field defaults empty when omitted
        assert resp.lens_coverage == {}

    def test_trip_generate_response_lens_coverage(self):
        """The response carries the engine's lens_coverage map."""
        resp = TripGenerateResponse(
            trip_id="trip-002",
            trip_name="Lens Trip",
            profile_id="prof-1",
            total_stops=0,
            total_duration_min=0,
            anchor_count=0,
            flavour_count=0,
            stops=[],
            lens_coverage={"dark_history": 4, "architecture": 2},
        )
        assert resp.lens_coverage == {"dark_history": 4, "architecture": 2}


class TestTripPreviewSpotlightFields:
    """The /trips/preview serializer types carry the spotlight
    fields — band and spotlight per stop, and the per-corridor lens note — with
    behavior-preserving defaults.

    The reply carries ROUTE OPTIONS rather than one flat stop list, so the lens note
    sits on the option it describes and the per-stop annotations ride
    ``RouteOptionStop``.
    """

    def _route_option(self, **overrides) -> RouteOption:
        kwargs = dict(
            route_id="preview-opt1",
            stops=(
                RouteOptionStop(
                    poi_id="poi-1",
                    name="Notre-Dame",
                    lat=48.853,
                    lng=2.349,
                    minutes=5,
                    band="dwell",
                ),
            ),
            eta_seconds=1800,
        )
        kwargs.update(overrides)
        return RouteOption(**kwargs)

    def test_preview_response_carries_only_what_planning_knows(self):
        """The plan-only preview response carries exactly what PLANNING knows.

        Nothing that describes WRITTEN text — no flat stop list, no narrator name,
        no quality verdict — rides this reply: planning writes nothing, so such a
        field would advertise something planning cannot produce.

        The reply also carries the pre-commit
        HONESTY SURFACE, all of it known at plan time and none of it authored
        text: the day's promises with their coarse windows, the plain-language
        day notes (closed doors, dial exclusions, unverified hours), the
        unplanned minutes as a number, and the longest single walk. Still
        exhaustive — the wire-contract twin (tests/test_trip_preview_contract.py)
        pins the same set on a real 200.
        """
        assert set(TripPreviewResponse.model_fields) == {
            "spine_area",
            "options",
            "tourability",
            "degradations",
            "promises",
            "day_notes",
            "slack_minutes",
            "longest_walk_minutes",
        }

    def test_lens_coverage_note_lives_on_the_option_it_describes(self):
        """The per-corridor lens note is a property of ONE route, not of the reply.

        It sat on the response while there was only ever one route to describe. With
        three routes offered at once a single note could only describe one of them,
        so it belongs to the option — and defaults to None when no lens was asked for.
        """
        assert "lens_coverage_note" not in TripPreviewResponse.model_fields
        assert self._route_option().lens_coverage_note is None
        noted = self._route_option(
            lens_coverage_note="2 of 5 places on this route speak to the chosen lens(es)."
        )
        assert "2 of 5" in noted.lens_coverage_note

    def test_preview_response_round_trips_with_its_options(self):
        """A full plan response survives a model_dump -> model_validate round-trip,
        proving the serializer carries the options and their per-stop annotations."""
        option = self._route_option(
            stops=(
                RouteOptionStop(
                    poi_id="poi-1",
                    name="Notre-Dame",
                    lat=48.853,
                    lng=2.349,
                    minutes=0,
                    band="vignette",
                    spotlight=0.5,
                ),
            )
        )
        resp = TripPreviewResponse(spine_area="Ile de la Cite", options=[option])
        rebuilt = TripPreviewResponse.model_validate(resp.model_dump())
        assert rebuilt == resp
        assert rebuilt.options[0].stops[0].band == "vignette"
        assert rebuilt.options[0].stops[0].spotlight == 0.5
        assert rebuilt.options[0].eta_seconds == 1800

    def test_preview_tourability_defaults_none_and_round_trips(self):
        """tourability defaults None (GREEN) — the additive field keeps old
        payload shapes valid — and a YELLOW payload survives the round-trip."""
        assert TripPreviewResponse().tourability is None
        resp = TripPreviewResponse(
            tourability=TripPreviewTourability(
                status="YELLOW",
                fill_ratio=0.73,
                anchor_candidates=1,
                reachable_poi_count=1,
                max_supportable_duration_min=44,
            ),
        )
        rebuilt = TripPreviewResponse.model_validate(resp.model_dump())
        assert rebuilt.tourability is not None
        assert rebuilt.tourability.status == "YELLOW"
        assert rebuilt.tourability.anchor_candidates == 1
        assert rebuilt.tourability.max_supportable_duration_min == 44

    def test_preview_tourability_payload_maps_engine_assessment(self):
        """The route handler glue (_tourability_payload) maps the engine's
        TourabilityAssessment onto the wire model — and passes None (GREEN)
        through untouched. This is the exact line that was missing when
        thin-area single-stop tours shipped with no warning."""
        from src.api.routes.trips import _tourability_payload
        from src.tour.contract import TourabilityAssessment

        assert _tourability_payload(None) is None

        assessment = TourabilityAssessment(
            status="YELLOW",
            walk_radius_m=738.0,
            fill_ratio=0.7315,
            dwell_capacity_seconds=1312,
            target_dwell_seconds=1793,
            reachable_poi_count=1,
            reachable_beat_count=32,
            anchor_candidate_count=1,
            cluster_compactness=0.0,
            duration_min=60,
            round_trip=False,
            max_supportable_duration_min=44,
            one_way_alternative_destination=None,
        )
        payload = _tourability_payload(assessment)
        assert payload is not None
        assert payload.status == "YELLOW"
        assert payload.fill_ratio == 0.73  # rounded for the wire
        assert payload.anchor_candidates == 1
        assert payload.reachable_poi_count == 1
        assert payload.max_supportable_duration_min == 44
        assert payload.one_way_alternative_destination is None

    def test_a_trip_saved_before_the_rename_still_discloses_its_thin_area(self):
        """A record written under the OLD field names must still restore.

        On 2026-08-06 two TourabilityAssessment fields were renamed as the
        planner's currency moved from narration seconds to dwell seconds.
        Every trip saved before that stored the old names, and the model is
        ``extra="forbid"``, so the stored dict no longer validates.

        WHY THIS TEST AND NOT A CODE READ. ``_restored_tourability`` fails
        OPEN — a ValidationError returns None, not a 500 — so the regression
        is invisible from the outside: no error, no log, the thin-area warning
        simply stops appearing on every trip saved before the rename. A test
        is the only thing that can see it.
        """
        from src.api.routes.trips import _restored_tourability, _tourability_payload

        stored_before_the_rename = {
            "status": "YELLOW",
            "walk_radius_m": 738.0,
            "fill_ratio": 0.7315,
            "dwell_capacity_seconds": 1312,
            "target_dwell_seconds": 1793,
            "reachable_poi_count": 1,
            "reachable_beat_count": 32,
            "anchor_candidate_count": 1,
            "cluster_compactness": 0.0,
            "duration_min": 60,
            "round_trip": False,
            "max_supportable_duration_min": 44,
            "one_way_alternative_destination": None,
            "delivered_thin": False,
            "on_lens_fill_ratio": None,
        }

        restored = _restored_tourability(stored_before_the_rename)
        assert restored is not None, (
            "a trip saved before the rename restored as None, so its thin-area "
            "disclosure silently vanished"
        )
        assert restored.status == "YELLOW"
        assert restored.dwell_capacity_seconds == 1312
        assert restored.target_dwell_seconds == 1793
        assert restored.max_supportable_duration_min == 44

        # And the disclosure the traveller actually sees survives the round trip.
        payload = _tourability_payload(restored)
        assert payload is not None
        assert payload.status == "YELLOW"
        assert payload.max_supportable_duration_min == 44

        # Fail-open is unchanged for a record that is genuinely broken rather
        # than merely old: a garbled row must still compose without a warning,
        # never a 500.
        assert _restored_tourability({"status": "YELLOW"}) is None
        assert _restored_tourability(None) is None
