"""Pydantic models for audio generation endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AudioPreviewRequest(BaseModel):
    """Request body for generating a TTS audio preview."""

    # Long per-stop narration is chunked by the provider (provider._split_for_tts),
    # so the cap is generous; it only guards absurd payloads, not real stops.
    text: str = Field(..., min_length=1, max_length=20000)
    provider: str | None = None
    voice_id: str | None = None


class ProviderInfo(BaseModel):
    """Metadata about an available TTS provider."""

    name: str
    available: bool


class ProviderListResponse(BaseModel):
    """List of registered TTS providers."""

    providers: list[ProviderInfo]


class CompareRequest(BaseModel):
    """Request body for comparing TTS output across providers."""

    text: str = Field(..., min_length=1, max_length=5000)
    providers: list[str] = Field(..., min_length=1, max_length=5)
    voice_ids: dict[str, str] = Field(
        default_factory=dict,
        description="Optional per-provider voice IDs, e.g. {'openai': 'nova'}",
    )


class CompareResultItem(BaseModel):
    """Result from a single provider in a comparison."""

    provider: str
    success: bool
    error: str | None = None
    size_bytes: int = 0
    duration_estimate_sec: float = 0.0
    download_path: str | None = None


class CompareResponse(BaseModel):
    """Side-by-side comparison results from multiple providers."""

    text: str
    results: list[CompareResultItem]


class EvalRequest(BaseModel):
    """Request body for evaluating TTS output against source text."""

    text: str = Field(..., min_length=1, max_length=5000)
    provider: str | None = None
    voice_id: str | None = None


class EvalResponse(BaseModel):
    """Evaluation result — how well the audio matches the source text."""

    provider: str
    original_text: str
    transcribed_text: str
    similarity_score: float = Field(..., description="0.0-1.0 Jaccard set similarity of words")
    word_error_rate: float = Field(..., description="0.0 = perfect, higher = worse")
    missing_words: list[str]
    extra_words: list[str]
    verdict: str = Field(..., description="PASS / REVIEW / FAIL based on WER thresholds")


class GenerateRequest(BaseModel):
    """Request body for a voicing pass — the per-stop trip voicing and the keep-exploring
    door (the per-beat generate routes were deleted)."""

    provider: str | None = None
    voice_id: str | None = None
    force: bool = Field(False, description="Force regeneration even if audio already exists")


class StopAudioResultItem(BaseModel):
    """Result for one stop in per-stop trip audio generation."""

    stop_id: str
    status: str = Field(description="generated | skipped | failed")
    audio_url: str | None = None
    reason: str | None = None
    error: str | None = None


class TripStopAudioGenerateResponse(BaseModel):
    """Results of generating one composed-narration MP3 per stop in a trip."""

    trip_id: str
    generated: int
    skipped: int
    failed: int
    results: list[StopAudioResultItem]


class StopAudioStatusResponse(BaseModel):
    """Per-stop audio status by ItineraryItem id."""

    stop_id: str
    has_audio: bool
    audio_url: str | None = None
    duration_sec: float | None = None


class KeepExploringAudioResponse(BaseModel):
    """On-demand audio for a stop's "keep exploring here" extra narration.

    Voiced OFF the tour budget (this is an on-demand deep-dive, not a scheduled
    stop), so it carries no duration_min / itinerary accounting. Mirrors the
    per-stop trip-audio contract: TTS failure returns 200 with status='failed'
    (never a 500), so a flaky provider is a soft, retryable result.
    """

    stop_id: str
    status: str = Field(description="generated | failed")
    audio_url: str | None = None
    duration_sec: float | None = None
    error: str | None = None
