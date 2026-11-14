"""Multi-city API. The trip endpoints carry a validated city_slug from the request
so New York (and future cities) can be toured. DB-free — pure request-model
validation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.models.trips import TripGenerateRequest, TripPreviewRequest
from src.tour.contract import SUPPORTED_CITIES


def _gen(**kw) -> dict:
    base = dict(
        profile_id="p1", center_lat=40.75, center_lng=-73.98,
        start_date="2026-08-01", end_date="2026-08-01",
    )
    base.update(kw)
    return base


def test_new_york_is_supported():
    assert {"paris", "new_york"} <= SUPPORTED_CITIES


def test_preview_defaults_to_paris_for_backcompat():
    assert TripPreviewRequest(center_lat=48.85, center_lng=2.36).city_slug == "paris"


def test_generate_defaults_to_paris_for_backcompat():
    assert TripGenerateRequest(**_gen()).city_slug == "paris"


@pytest.mark.parametrize("model,kw", [
    (TripPreviewRequest, dict(center_lat=40.75, center_lng=-73.98)),
    (TripGenerateRequest, _gen()),
])
def test_new_york_is_accepted_and_normalized(model, kw):
    assert model(city_slug="new_york", **kw).city_slug == "new_york"
    assert model(city_slug="  New_York ", **kw).city_slug == "new_york"  # trimmed + lowercased


@pytest.mark.parametrize("model,kw", [
    (TripPreviewRequest, dict(center_lat=40.75, center_lng=-73.98)),
    (TripGenerateRequest, _gen()),
])
def test_unknown_city_is_rejected(model, kw):
    # An unknown city is a clear validation error (422 at the API), not a silent
    # empty-corpus tour.
    with pytest.raises(ValidationError):
        model(city_slug="atlantis", **kw)
