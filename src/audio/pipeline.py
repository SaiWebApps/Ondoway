"""Audio pipeline — TTS generation and storage for ONE STOP's narration.

Pipeline: a stop's stitched narration → TTS Provider → Storage → the caller persists
the url and the MEASURED duration on the ItineraryItem (src/api/routes/audio.py).

The per-beat library — ``generate_beat_audio`` / ``generate_batch`` / ``check_audio_status``
over ``NarrativeBeat.audio_url`` — was DELETED: the per-stop
path (``generate_stop_audio``) and the keep-exploring door are the only voicing doors.

Usage:
    from src.audio.pipeline import generate_stop_audio
    result = generate_stop_audio(narration, stop_key=item_id, poi_name=name)
"""

from __future__ import annotations

import struct
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from typing import Protocol

from src.audio.provider import TTSError, get_provider_with_fallback
from src.audio.storage import StorageError, get_storage


class PipelineError(Exception):
    """Raised when the audio generation pipeline fails."""


def _get_duration(audio_bytes: bytes) -> float:
    """Extract duration in seconds from audio bytes (WAV or MP3).

    WAV: parsed exactly via stdlib wave module.
    MP3: MEASURED from the frame table — every Layer III frame
    walked and its samples summed, exact for constant- and variable-bitrate files
    and for MPEG-1, MPEG-2 and MPEG-2.5 alike.
    Returns 0.0 if the format cannot be determined.
    """
    # Try WAV first (starts with RIFF header)
    if audio_bytes[:4] == b"RIFF":
        try:
            with wave.open(BytesIO(audio_bytes), "rb") as wf:
                return wf.getnframes() / wf.getframerate()
        except Exception:
            return 0.0

    return _mp3_duration(audio_bytes)


# ── MPEG audio frame tables, Layer III (ISO/IEC 11172-3 and 13818-3) ──
# Bitrates in kbps by header index; MPEG-2 and MPEG-2.5 share the low table.
_L3_BITRATES_KBPS: dict[int, tuple[int, ...]] = {
    1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0),
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0),
}
_SAMPLE_RATES: dict[int, tuple[int, int, int]] = {
    1: (44100, 48000, 32000),
    2: (22050, 24000, 16000),
    25: (11025, 12000, 8000),
}
_SAMPLES_PER_FRAME: dict[int, int] = {1: 1152, 2: 576, 25: 576}
# Bytes a sync scan may wander before the first frame (a stray prefix, not a tag).
_MP3_SYNC_SCAN_BYTES = 8192


def _id3v2_size(data: bytes) -> int:
    """Bytes of a leading ID3v2 tag (header + syncsafe size), or 0."""
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    size = 0
    for b in data[6:10]:
        if b & 0x80:
            return 0  # not syncsafe: not a tag we trust; scan from the start
        size = (size << 7) | b
    return 10 + size


def _l3_frame(data: bytes, i: int) -> tuple[int, float] | None:
    """The Layer III frame at ``data[i:]`` as (byte length, seconds), or None when no
    valid frame header starts there. Sync is 11 set bits; version 01 is reserved."""
    if i + 4 > len(data):
        return None
    hdr = struct.unpack(">I", data[i : i + 4])[0]
    if (hdr >> 21) != 0x7FF:
        return None
    version_bits = (hdr >> 19) & 0x3
    layer_bits = (hdr >> 17) & 0x3
    if layer_bits != 1 or version_bits == 1:  # Layer III only; reserved version
        return None
    version = {3: 1, 2: 2, 0: 25}[version_bits]
    bitrate_idx = (hdr >> 12) & 0xF
    sr_idx = (hdr >> 10) & 0x3
    padding = (hdr >> 9) & 0x1
    if bitrate_idx in (0, 15) or sr_idx == 3:
        return None
    bitrate_bps = _L3_BITRATES_KBPS[1 if version == 1 else 2][bitrate_idx] * 1000
    rate = _SAMPLE_RATES[version][sr_idx]
    per_frame = 144 if version == 1 else 72
    length = per_frame * bitrate_bps // rate + padding
    return length, _SAMPLES_PER_FRAME[version] / rate


def _mp3_duration(data: bytes) -> float:
    """MEASURE an MP3's duration from its frame table.

    Skips a leading ID3v2 tag, scans to the first valid Layer III frame, then walks
    frame by frame — each frame's length from its own header, each frame's seconds
    as samples / sample rate — and sums. Exact for CBR and VBR, MPEG-1/2/2.5. A
    truncated tail counts no frame; the walk ends at the first non-frame after the
    stream (an ID3v1 tag, trailing garbage). 0.0 when no frame is found. Before the
    frame walk this read ONE header and divided the file size by that bitrate: a VBR file was off
    by the bitrate ratio and every MPEG-2 / 2.5 file measured 0.0 — the phone's
    sentence arithmetic then cut at the wrong place, or at once.
    """
    i = _id3v2_size(data)
    end = len(data)
    total = 0.0
    found = False
    scan_until = min(end, i + _MP3_SYNC_SCAN_BYTES)
    while i + 4 <= end:
        frame = _l3_frame(data, i)
        if frame is None:
            if found or i >= scan_until:
                break
            i += 1
            continue
        length, seconds = frame
        if i + length > end:
            break
        found = True
        total += seconds
        i += length
    return total


@dataclass
class StopAudioResult:
    """Result of generating audio for one stop's stitched narration."""

    stop_key: str
    provider: str
    storage: str
    audio_url: str
    size_bytes: int
    duration_sec: float


def _build_stop_storage_key(stop_key: str, poi_name: str | None = None) -> str:
    """Deterministic storage key for a stop's narration audio: stops/{slug}/{key}.mp3."""
    slug = "unknown"
    if poi_name:
        slug = poi_name.lower().replace(" ", "_").replace("'", "")
    return f"stops/{slug}/{stop_key}.mp3"


class StoredAudioRef(Protocol):
    """An audio file already in storage, named by the URL its upload returned."""

    audio_url: str


def generate_stop_audio(
    narration: str,
    *,
    stop_key: str,
    poi_name: str | None = None,
    provider_name: str | None = None,
    storage_name: str | None = None,
    voice_id: str | None = None,
) -> StopAudioResult:
    """Generate + store TTS audio for one stop's stitched narration.

    Mirrors ``generate_beat_audio`` but voices the per-stop
    narration (cold-open + transit glue + beats + closing) instead of a single
    beat, and does NOT touch Neo4j — the prepare-trip wiring and per-stop
    persistence live with the caller. ``MockTTSProvider`` + ``LocalStorageProvider``
    are the offline defaults so ``make test`` stays free.
    """
    if not narration or not narration.strip():
        raise PipelineError(f"Stop '{stop_key}' has empty narration")
    return assemble_stop_audio(
        [narration],
        stop_key=stop_key,
        poi_name=poi_name,
        provider_name=provider_name,
        storage_name=storage_name,
        voice_id=voice_id,
    )


def assemble_stop_audio(
    parts: Sequence[str | StoredAudioRef],
    *,
    stop_key: str,
    poi_name: str | None = None,
    provider_name: str | None = None,
    storage_name: str | None = None,
    voice_id: str | None = None,
) -> StopAudioResult:
    """One stop's audio file, built in playing order from text to voice now and audio
    files already stored, joined frame to frame the way long text's chunks are.

    Only the text parts reach the voice provider, so a stop whose stored pieces cover
    most of its words pays only for the rest. A blank text part is skipped. The joined
    file's duration is measured from its own frames.
    """
    spoken = [part for part in parts if not isinstance(part, str) or part.strip()]
    if not spoken:
        raise PipelineError(f"Stop '{stop_key}' has empty narration")

    # Resolution is inside the try so an unknown provider name (ValueError)
    # becomes a PipelineError — callers turn that into a soft per-stop failure
    # (keep-exploring: 200 status='failed'; trip-stops: per-stop failed), never
    # an uncaught 500.
    #
    # ...with_fallback, not get_provider: this is the TOURIST's audio, so an
    # unnamed provider resolves to the pin PLUS its TTS_FALLBACK understudies and
    # a vendor outage costs a different narrator rather than silence. A caller
    # that names a provider still gets exactly that one. ``result.provider``
    # below is therefore who actually spoke, not who was asked.
    provider = None
    if any(isinstance(part, str) for part in spoken):
        try:
            provider = get_provider_with_fallback(provider_name)
        except ValueError as e:
            raise PipelineError(f"TTS failed for stop '{stop_key}': {e}") from e

    # get_storage() is inside the try for the same reason as get_provider():
    # a misconfigured storage backend must surface as a soft per-stop failure.
    try:
        storage = get_storage(storage_name)
    except (StorageError, ValueError, OSError) as e:
        raise PipelineError(f"Storage failed for stop '{stop_key}': {e}") from e

    chunks: list[bytes] = []
    for part in spoken:
        try:
            if isinstance(part, str) and provider is not None:
                data = provider.generate(part, voice_id=voice_id)
            elif not isinstance(part, str):
                data = storage.read(part.audio_url)
            else:
                raise PipelineError(f"No voice resolved for stop '{stop_key}'")
        except TTSError as e:
            raise PipelineError(f"TTS failed for stop '{stop_key}': {e}") from e
        except StorageError as e:
            raise PipelineError(f"Storage failed for stop '{stop_key}': {e}") from e
        # A later file's leading tag would sit mid-stream, where players and the
        # frame walk that measures duration stop reading.
        chunks.append(data if not chunks else data[_id3v2_size(data) :])
    audio_bytes = b"".join(chunks)

    key = _build_stop_storage_key(stop_key, poi_name)
    try:
        audio_url = storage.upload(audio_bytes, key)
    except StorageError as e:
        raise PipelineError(f"Storage failed for stop '{stop_key}': {e}") from e

    duration = round(_get_duration(audio_bytes), 2)
    return StopAudioResult(
        stop_key=stop_key,
        provider=provider.name if provider is not None else "stored",
        storage=storage.name,
        audio_url=audio_url,
        size_bytes=len(audio_bytes),
        duration_sec=duration,
    )
