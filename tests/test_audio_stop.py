"""generate_stop_audio: TTS a stop's stitched narration.

Unit tests patch provider/storage (no I/O); the integration test uses the real
LocalStorageProvider (in a tmp dir) + the real MockTTSProvider to prove the
provider→storage wiring end to end. No Neo4j (nothing here persists).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.audio.pipeline import PipelineError, generate_stop_audio
from src.audio.provider import MockTTSProvider


def test_narration_is_sent_to_tts_and_result_returned() -> None:
    fake_provider = MagicMock()
    fake_provider.name = "mock"
    # Valid WAV bytes so _get_duration() parses a real (>0) duration.
    fake_provider.generate.return_value = MockTTSProvider().generate("one two three four five six")
    fake_storage = MagicMock()
    fake_storage.name = "local"
    fake_storage.upload.return_value = "/api/v1/audio/files/stops/marais/s1.mp3"

    narration = "Settle in. Welcome to the Marais. Walk on toward the square."
    with (
        patch("src.audio.pipeline.get_provider_with_fallback", return_value=fake_provider),
        patch("src.audio.pipeline.get_storage", return_value=fake_storage),
    ):
        result = generate_stop_audio(narration, stop_key="s1", poi_name="Le Marais")

    # The exact stitched narration is what gets voiced — not a lone beat.
    fake_provider.generate.assert_called_once()
    assert fake_provider.generate.call_args.args[0] == narration
    assert result.audio_url == "/api/v1/audio/files/stops/marais/s1.mp3"
    assert result.duration_sec > 0
    assert result.size_bytes > 0
    assert result.provider == "mock"
    assert result.storage == "local"


def test_empty_narration_raises() -> None:
    with pytest.raises(PipelineError):
        generate_stop_audio("   ", stop_key="s1")


def test_two_stops_produce_two_distinct_artifacts(tmp_path, monkeypatch) -> None:
    # Real LocalStorageProvider writes under AUDIO_STORAGE_PATH (read at init).
    monkeypatch.setenv("AUDIO_STORAGE_PATH", str(tmp_path))

    r1 = generate_stop_audio(
        "Settle in. This is the first stop.",
        stop_key="s1",
        provider_name="mock",
        storage_name="local",
    )
    r2 = generate_stop_audio(
        "Now walk on to the second stop and listen.",
        stop_key="s2",
        provider_name="mock",
        storage_name="local",
    )

    assert r1.audio_url != r2.audio_url
    assert r1.duration_sec > 0 and r2.duration_sec > 0

    files = list(tmp_path.rglob("*.mp3"))
    assert len(files) == 2
    assert all(f.stat().st_size > 0 for f in files)
