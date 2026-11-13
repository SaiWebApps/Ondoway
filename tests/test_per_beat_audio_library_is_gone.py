"""The per-beat audio library is deleted: the per-beat
audio routes, pipeline and models are GONE, and the phone no longer polls per beat. The only
voicing doors are the per-stop path and the keep-exploring door.

The admin gate is ON here so a 404 proves ABSENCE, not the gate (the lesson in
tests/test_audio_route_hardening.py). Free tier: the app is built with the driver patched out;
no graph, no provider.
"""

from __future__ import annotations

import pathlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app

ROOT = pathlib.Path(__file__).resolve().parents[1]
MOBILE_LIB = ROOT / "mobile" / "lib"

GONE_PATHS = (
    "/api/v1/audio/status/{beat_id}",
    "/api/v1/audio/generate/{beat_id}",
    "/api/v1/audio/generate-batch",
    "/api/v1/audio/generate-trip/{trip_id}",
)
SURVIVING_PATHS = (
    "/api/v1/audio/generate-trip-stops/{trip_id}",
    "/api/v1/audio/stop-status/{stop_id}",
    "/api/v1/audio/stops/{stop_id}/keep-exploring",
    "/api/v1/audio/preview",
    "/api/v1/audio/providers",
    "/api/v1/audio/files/{key:path}",
)


@pytest.fixture()
def admin_app(monkeypatch):
    monkeypatch.setenv("WORKBENCH_API_ENABLED", "true")
    with patch("src.api.app.init_driver"), patch("src.api.app.close_driver"):
        app = create_app()
        with TestClient(app) as client:
            yield app, client


def test_the_per_beat_routes_are_not_mounted_and_answer_a_plain_404(admin_app):
    app, client = admin_app
    paths = {getattr(r, "path", None) for r in app.routes}
    for gone in GONE_PATHS:
        assert gone not in paths, f"{gone} is still mounted"
    for kept in SURVIVING_PATHS:
        assert kept in paths, f"{kept} vanished — the per-stop doors must survive"
    # A request answers the router's own 404 (no route), never a beat-level message.
    for url in (
        "/api/v1/audio/status/some-beat",
        "/api/v1/audio/generate-trip/some-trip",
    ):
        resp = client.get(url) if "status" in url else client.post(url, json={})
        assert resp.status_code == 404, f"{url} -> {resp.status_code}: {resp.text}"
        assert resp.json().get("detail") == "Not Found", resp.text
    assert client.post("/api/v1/audio/generate/some-beat", json={}).status_code == 404
    assert client.post("/api/v1/audio/generate-batch", json={}).status_code == 404


def test_the_per_beat_pipeline_and_models_are_gone_and_the_per_stop_path_stands():
    import src.audio as audio_pkg
    from src.api.models import audio as audio_models
    from src.audio import pipeline

    for name in (
        "generate_beat_audio",
        "generate_batch",
        "check_audio_status",
        "_build_storage_key",
        "_script_hash",
        "_is_stale",
        "GenerationResult",
        "AudioStatus",
        "BatchSummary",
    ):
        assert not hasattr(pipeline, name), f"pipeline still carries {name}"
    for name in (
        "AudioStatusResponse",
        "GenerateResponse",
        "BatchGenerateRequest",
        "BatchResultItem",
        "BatchGenerateResponse",
        "TripAudioResultItem",
        "TripAudioGenerateResponse",
    ):
        assert not hasattr(audio_models, name), f"models still carry {name}"
    assert "generate_beat_audio" not in audio_pkg.__all__
    assert "generate_batch" not in audio_pkg.__all__
    # The survivors: one voicing path, one request shape, one storage key helper.
    assert callable(pipeline.generate_stop_audio)
    assert callable(pipeline._build_stop_storage_key)
    assert hasattr(audio_models, "GenerateRequest")
    assert hasattr(audio_models, "TripStopAudioGenerateResponse")
    assert hasattr(audio_models, "KeepExploringAudioResponse")


def test_the_phone_polls_per_stop_only():
    audio = (MOBILE_LIB / "services" / "audio_service.dart").read_text(encoding="utf-8")
    page = (MOBILE_LIB / "pages" / "trip_itinerary_page.dart").read_text(encoding="utf-8")
    assert "checkAudioStatus(" not in audio and "/audio/status/" not in audio, (
        "the per-beat status poll is still on the phone"
    )
    assert "checkStopAudioStatus(" in audio, "the per-stop poll must survive"
    assert "checkAudioStatus(" not in page, "the itinerary page still falls back per beat"
    assert "checkStopAudioStatus(" in page
