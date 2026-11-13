"""Unit tests for the audio generation pipeline.

Uses mocked Neo4j sessions — does NOT require a running database.

TOMBSTONE: the per-beat audio library is deleted. The
per-beat library's tests were DELETED here, not adapted: ``TestBuildStorageKey``,
``TestGenerateBeatAudio``, ``TestGenerateBatch``,
``TestGetDuration::test_result_includes_duration``, ``TestScriptChangeDetection``,
``TestPipelineErrorWrapping``, ``TestBatchMixedResults``,
``TestScriptHashDeterminism``, ``TestStaleInvariant`` and the beat half of
``TestStorageConstructionSoftFails`` all pinned ``generate_beat_audio`` /
``generate_batch`` / ``check_audio_status`` / ``_build_storage_key`` /
``_script_hash`` / ``_is_stale`` — the NarrativeBeat audio path replaced
by the per-stop path (``generate_stop_audio``: tests/test_audio_stop.py and
tests/test_audio_stop_trip_api.py). What survives below is the format-level
duration reader (its MP3 half MEASURES the duration, and the clocks trust it) and
the per-stop storage soft-failure.
"""

from __future__ import annotations

import pytest

from src.audio.pipeline import PipelineError, _get_duration


@pytest.fixture(autouse=True)
def _temp_storage(monkeypatch, tmp_path):
    """Use a temp directory for audio storage in all tests."""
    monkeypatch.setenv("AUDIO_STORAGE_PATH", str(tmp_path))


class TestGetDuration:
    def test_wav_duration(self):
        """Mock provider generates WAV — duration should match expected."""
        from src.audio.provider import MockTTSProvider

        provider = MockTTSProvider()
        # "one two three four five" = 5 words → 5/2.5 = 2.0 seconds
        audio = provider.generate("one two three four five")
        duration = _get_duration(audio)
        assert abs(duration - 2.0) < 0.1

    def test_wav_minimum_duration(self):
        """Short text should still produce at least 1 second."""
        from src.audio.provider import MockTTSProvider

        provider = MockTTSProvider()
        audio = provider.generate("hi")
        duration = _get_duration(audio)
        assert abs(duration - 1.0) < 0.1

    def test_unknown_format_returns_zero(self):
        """Garbage bytes should return 0.0, not crash."""
        assert _get_duration(b"not audio data at all") == 0.0

    def test_empty_bytes_returns_zero(self):
        assert _get_duration(b"") == 0.0


# ── Storage misconfiguration must be a soft failure, never a 500 ──


class TestStorageConstructionSoftFails:
    """get_storage() must be inside the guarded block of the per-stop path.

    StorageError/ValueError are not PipelineError, so an unknown or broken
    storage backend escaped the `except PipelineError` handlers in
    src/api/routes/audio.py and surfaced as an uncaught HTTP 500 — violating
    the documented "TTS failure returns 200 with status=failed (never a 500)"
    contract.
    """

    def test_unknown_storage_stop_path_raises_pipeline_error(self, monkeypatch):
        from src.audio.pipeline import generate_stop_audio

        monkeypatch.setenv("AUDIO_STORAGE", "definitely-not-a-backend")
        with pytest.raises(PipelineError, match="Storage failed for stop"):
            generate_stop_audio("Some narration.", stop_key="stop-1", provider_name="mock")


# ── A vendor outage costs a different voice, not silence ──


class TestStopAudioFallsOverToTheUnderstudy:
    """The per-stop path is THE tourist's audio, so it resolves the whole chain.

    OpenAI answered an intermittent 500 on 2026-08-28 — their outage, their
    words. ``generate_stop_audio`` therefore resolves through
    ``get_provider_with_fallback``: an UNNAMED provider means the pin plus its
    ``TTS_FALLBACK`` understudies, and the result records who actually spoke.

    UNDO TEST: put ``get_provider`` back in pipeline.py -> RED (PipelineError).
    """

    def test_an_unnamed_provider_falls_over_and_records_the_real_voice(self, monkeypatch):
        from src.audio.pipeline import generate_stop_audio
        from src.audio.provider import _PROVIDERS, TTSError

        class _Down:
            @property
            def name(self) -> str:
                return "down"

            def generate(self, text: str, *, voice_id: str | None = None) -> bytes:
                raise TTSError("500 The server had an error while processing your request")

        monkeypatch.setitem(_PROVIDERS, "down", _Down)
        monkeypatch.setenv("TTS_PROVIDER", "down")
        monkeypatch.setenv("TTS_FALLBACK", "mock")

        result = generate_stop_audio("Some narration.", stop_key="stop-1")

        assert result.provider == "mock", "the stop must record the voice that ACTUALLY spoke"
        assert result.size_bytes > 0
        assert result.duration_sec > 0

    def test_a_named_provider_still_fails_rather_than_substituting_a_voice(self, monkeypatch):
        from src.audio.pipeline import generate_stop_audio
        from src.audio.provider import _PROVIDERS, TTSError

        class _Down:
            @property
            def name(self) -> str:
                return "down"

            def generate(self, text: str, *, voice_id: str | None = None) -> bytes:
                raise TTSError("500 The server had an error while processing your request")

        monkeypatch.setitem(_PROVIDERS, "down", _Down)
        monkeypatch.setenv("TTS_FALLBACK", "mock")

        with pytest.raises(PipelineError, match="TTS failed for stop"):
            generate_stop_audio("Some narration.", stop_key="stop-1", provider_name="down")


# ── The MP3 duration is MEASURED from the frame table ─────────────────────────────


def _mp3_frame(*, version: str, bitrate_kbps: int, sample_rate: int, padding: int = 0) -> bytes:
    """One Layer III frame: a 4-byte header and a zero payload of the exact frame length,
    built from the MPEG audio spec (the same arithmetic the reader must perform)."""
    version_bits = {"1": 0b11, "2": 0b10, "2.5": 0b00}[version]
    table = {
        "1": [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
        "2": [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
        "2.5": [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
    }[version]
    rates = {
        "1": [44100, 48000, 32000],
        "2": [22050, 24000, 16000],
        "2.5": [11025, 12000, 8000],
    }[version]
    bitrate_idx = table.index(bitrate_kbps)
    sr_idx = rates.index(sample_rate)
    header = (
        (0x7FF << 21) | (version_bits << 19) | (0b01 << 17) | (1 << 16)  # sync, version, L3, no CRC
        | (bitrate_idx << 12) | (sr_idx << 10) | (padding << 9) | (0b00 << 6)  # stereo
    )
    per_frame = 144 if version == "1" else 72
    length = per_frame * bitrate_kbps * 1000 // sample_rate + padding
    return header.to_bytes(4, "big") + bytes(length - 4)


def _samples(version: str) -> int:
    return 1152 if version == "1" else 576


class TestMeasuredMp3Duration:
    """The duration is MEASURED, and the clocks trust it: the
    reader walks EVERY frame and sums samples / sample rate — exact for constant- and
    variable-bitrate files and for MPEG-1, MPEG-2 and MPEG-2.5 alike, skipping an ID3v2
    tag. It used to read one header and divide the file size by that bitrate: a VBR
    file was off by the bitrate ratio and an MPEG-2 file measured 0.0 (the phone then cut
    its sentences at the wrong place, or at once)."""

    def test_a_constant_bitrate_mpeg1_file_measures_its_frame_count_exactly(self):
        data = b"".join(
            _mp3_frame(version="1", bitrate_kbps=128, sample_rate=44100) for _ in range(300)
        )
        assert abs(_get_duration(data) - 300 * 1152 / 44100) < 0.01

    def test_a_variable_bitrate_file_is_the_sum_of_its_frames_not_a_first_frame_guess(self):
        frames = []
        for i in range(400):
            frames.append(
                _mp3_frame(version="1", bitrate_kbps=(64 if i % 2 else 256), sample_rate=44100)
            )
        data = b"".join(frames)
        exact = 400 * 1152 / 44100
        assert abs(_get_duration(data) - exact) < 0.01
        # The first-frame guess (size * 8 / 256 kbps) is ~37% short — the defect.
        assert abs((len(data) * 8 / 256000) - exact) > 2.0

    def test_an_mpeg2_low_sample_rate_file_measures_too(self):
        data = b"".join(
            _mp3_frame(version="2", bitrate_kbps=64, sample_rate=22050) for _ in range(200)
        )
        assert abs(_get_duration(data) - 200 * 576 / 22050) < 0.01
        data25 = b"".join(
            _mp3_frame(version="2.5", bitrate_kbps=32, sample_rate=11025) for _ in range(100)
        )
        assert abs(_get_duration(data25) - 100 * 576 / 11025) < 0.01

    def test_padding_bits_and_an_id3v2_tag_do_not_throw_the_walk(self):
        body = b"".join(
            _mp3_frame(version="1", bitrate_kbps=128, sample_rate=44100, padding=i % 2)
            for i in range(120)
        )
        # ID3v2.3 header: 'ID3', version, flags, 4 syncsafe size bytes, then the tag bytes.
        tag_size = 300
        id3 = b"ID3\x03\x00\x00" + bytes([0, 0, tag_size >> 7, tag_size & 0x7F]) + bytes(tag_size)
        assert abs(_get_duration(id3 + body) - 120 * 1152 / 44100) < 0.01
        assert abs(_get_duration(body) - 120 * 1152 / 44100) < 0.01

    def test_a_truncated_tail_counts_only_the_whole_frames(self):
        whole = b"".join(
            _mp3_frame(version="1", bitrate_kbps=128, sample_rate=44100) for _ in range(50)
        )
        assert abs(_get_duration(whole + b"\xff\xfb\x90") - 50 * 1152 / 44100) < 0.01
