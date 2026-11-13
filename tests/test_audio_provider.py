"""Unit tests for audio TTS providers.

These tests do NOT require API keys or external services — all real providers
are tested for graceful failure, and the mock provider is tested for correctness.
"""

from __future__ import annotations

import wave
from io import BytesIO

import httpx
import pytest

from src.api.models.audio import AudioPreviewRequest
from src.audio.provider import (
    _PROVIDERS,
    MAX_TTS_CHARS,
    ElevenLabsTTSProvider,
    FailoverTTSProvider,
    MockTTSProvider,
    OpenAITTSProvider,
    TTSError,
    TTSProvider,
    _split_for_tts,
    get_provider,
    get_provider_with_fallback,
    list_providers,
)


class _Resp:
    """Minimal stand-in for an httpx.Response with a 200 + content."""

    status_code = 200
    content = b"audio-bytes"


class TestProviderRegistry:
    def test_list_providers_returns_all(self):
        """The real providers ship registered; the silent double is registered
        by tests/conftest.py and by nothing else.

        Owner decision, 2026-07-31: ``_PROVIDERS`` is the workbench's dropdown and
        the set of values POST /audio/preview honours, so ``MockTTSProvider`` is
        no longer in it. conftest calls ``register_provider`` at import, which is
        why "mock" resolves in a pytest interpreter — and does not in any uvicorn
        one. ``test_workbench_matches_the_app`` proves that second half by
        probing a real server-shaped subprocess.
        """
        names = list_providers()
        assert "openai" in names
        assert "elevenlabs" in names
        assert "mock" in names, (
            "tests/conftest.py registers the silent double for the pytest "
            "process; without it the whole audio suite would need paid TTS"
        )

    def test_get_provider_mock(self):
        p = get_provider("mock")
        assert isinstance(p, MockTTSProvider)
        assert isinstance(p, TTSProvider)

    def test_get_provider_openai(self):
        p = get_provider("openai")
        assert isinstance(p, OpenAITTSProvider)
        assert isinstance(p, TTSProvider)

    def test_get_provider_elevenlabs(self):
        p = get_provider("elevenlabs")
        assert isinstance(p, ElevenLabsTTSProvider)
        assert isinstance(p, TTSProvider)

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown TTS provider"):
            get_provider("nonexistent")

    def test_an_unpinned_process_refuses_to_pick_a_provider(self, monkeypatch):
        """FAIL CLOSED. An unset TTS_PROVIDER must raise, not choose silently.

        This test used to assert the opposite ("the default provider is mock"),
        which pinned the defect the owner found on 2026-07-31: the workbench
        server set no TTS_PROVIDER, inherited that "mock" default, and answered
        every /audio request with a SILENT WAV an editor could mistake for real
        narration. A server that is misconfigured must say so.

        UNDO TEST: restore a default in ``get_provider`` -> RED.
        """
        monkeypatch.delenv("TTS_PROVIDER", raising=False)
        with pytest.raises(ValueError, match="No TTS provider selected"):
            get_provider()

    def test_env_var_selects_provider(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "openai")
        p = get_provider()
        assert p.name == "openai"


class TestMockProvider:
    def test_name(self):
        assert MockTTSProvider().name == "mock"

    def test_generates_valid_wav(self):
        audio = MockTTSProvider().generate("Hello world")
        with wave.open(BytesIO(audio), "rb") as wf:
            assert wf.getnchannels() == 2  # stereo
            assert wf.getframerate() == 44100
            assert wf.getsampwidth() == 2  # 16-bit

    def test_duration_scales_with_text(self):
        short = MockTTSProvider().generate("Hi")
        long = MockTTSProvider().generate("This is a much longer piece of text " * 10)
        assert len(long) > len(short)

    def test_minimum_duration(self):
        audio = MockTTSProvider().generate("Hi")
        with wave.open(BytesIO(audio), "rb") as wf:
            duration = wf.getnframes() / wf.getframerate()
            assert duration >= 1.0

    def test_voice_id_accepted(self):
        # voice_id is ignored but shouldn't error
        audio = MockTTSProvider().generate("Hello", voice_id="test-voice")
        assert len(audio) > 0

    def test_output_size_bounded_for_huge_input(self):
        """DoS guard: a 10k-word input must NOT allocate a word-scaled WAV.

        Without the duration clamp, 10,000 words -> ~705 MB per request (and the
        bytes are then returned as the HTTP body). The mock is not run through
        _split_for_tts, so the WAV size must be capped independent of input.
        A 60s silent WAV (44100 * 2ch * 2B * 60) is ~10.6 MB — a generous ceiling
        that still fails the unbounded original (~705 MB for 10k words).
        """
        max_bytes = 44100 * 2 * 2 * 60 + 4096  # ~10.6 MB + WAV header slack
        huge = "word " * 10_000  # 10k words -> ~705 MB before the fix
        audio = MockTTSProvider().generate(huge)
        assert len(audio) <= max_bytes, (
            f"mock WAV must be bounded regardless of input length; "
            f"got {len(audio)} bytes (> {max_bytes})"
        )

    def test_duration_capped_regardless_of_word_count(self):
        """The synthesized duration must not grow unboundedly with word count.

        The pre-fix heuristic (word_count / 2.5) gives 10k words -> 4000s. The
        duration must be clamped to a small ceiling instead.
        """
        audio = MockTTSProvider().generate("word " * 10_000)
        with wave.open(BytesIO(audio), "rb") as wf:
            duration = wf.getnframes() / wf.getframerate()
        assert duration <= 60.0, f"duration must be bounded; got {duration}s"


class TestOpenAIProvider:
    def test_name(self):
        assert OpenAITTSProvider().name == "openai"

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(TTSError, match="OPENAI_API_KEY not set"):
            OpenAITTSProvider().generate("test")


class TestElevenLabsProvider:
    def test_name(self):
        assert ElevenLabsTTSProvider().name == "elevenlabs"

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        with pytest.raises(TTSError, match="ELEVENLABS_API_KEY not set"):
            ElevenLabsTTSProvider().generate("test")

    def test_missing_voice_id_raises(self, monkeypatch):
        monkeypatch.setenv("ELEVENLABS_API_KEY", "fake-key")
        monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
        with pytest.raises(TTSError, match="ELEVENLABS_VOICE_ID not set"):
            ElevenLabsTTSProvider().generate("test")


class TestTTSRetry:
    """Transient timeouts are retried with backoff (offline, mocked)."""

    def test_openai_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        monkeypatch.setattr("src.audio._http.time.sleep", lambda *_: None)
        calls = {"n": 0}

        def fake_post(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:  # fail twice, then succeed
                raise httpx.TimeoutException("transient")
            return _Resp()

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        out = OpenAITTSProvider().generate("hello")
        assert out == b"audio-bytes"
        assert calls["n"] == 3

    def test_openai_raises_ttserror_after_exhausting_retries(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        monkeypatch.setattr("src.audio._http.time.sleep", lambda *_: None)

        def always_timeout(self, *args, **kwargs):
            raise httpx.TimeoutException("transient")

        monkeypatch.setattr(httpx.Client, "post", always_timeout)
        with pytest.raises(TTSError, match="after 3 attempts"):
            OpenAITTSProvider().generate("hello")

    def test_elevenlabs_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
        monkeypatch.setenv("ELEVENLABS_VOICE_ID", "v")
        monkeypatch.setattr("src.audio._http.time.sleep", lambda *_: None)
        calls = {"n": 0}

        def fake_post(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 2:  # one ConnectError, then succeed
                raise httpx.ConnectError("transient")
            return _Resp()

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        out = ElevenLabsTTSProvider().generate("hello")
        assert out == b"audio-bytes"
        assert calls["n"] == 2


class TestSplitForTts:
    """Long narration is chunked under the TTS input cap (Fix: the 422 on long stops)."""

    def test_short_text_is_one_chunk(self):
        assert _split_for_tts("Hello there.") == ["Hello there."]

    def test_empty_text_is_no_chunks(self):
        assert _split_for_tts("   ") == []

    def test_long_text_splits_on_sentences_under_cap(self):
        text = "A sentence about Paris and its long history. " * 200  # ~9000 chars
        chunks = _split_for_tts(text, max_chars=1000)
        assert len(chunks) > 1
        assert all(len(c) <= 1000 for c in chunks)
        # no sentences lost across the split
        assert sum(c.count("Paris") for c in chunks) == 200

    def test_overlong_single_sentence_is_hard_split(self):
        text = "x" * 2500  # no sentence boundary to split on
        chunks = _split_for_tts(text, max_chars=1000)
        assert len(chunks) == 3
        assert all(len(c) <= 1000 for c in chunks)
        assert "".join(chunks) == text


class TestTTSChunking:
    def test_openai_chunks_long_text_and_concatenates(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        inputs: list[str] = []

        def fake_post(self, *args, **kwargs):
            inputs.append(kwargs["json"]["input"])

            class _R:
                status_code = 200
                content = f"<{len(inputs)}>".encode()

            return _R()

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        long_text = "A sentence about Paris and its remarkable history. " * 120  # ~6000 chars

        out = OpenAITTSProvider().generate(long_text)

        assert len(inputs) >= 2, "long text must be chunked into multiple TTS calls"
        assert all(len(t) <= MAX_TTS_CHARS for t in inputs), "each chunk under the cap"
        assert out == b"".join(f"<{i + 1}>".encode() for i in range(len(inputs)))


class TestOpenAIModelChoice:
    """The default model is a BUG FIX, not a preference (measured 2026-08-28).

    Against a live key, `tts-1-hd` answered 500 "The server had an error while
    processing your request" on three of four attempts at this repo's own Café
    de Flore Occupation beat — and on each of that beat's sentences alone —
    while the Eiffel beat voiced fine on the same key in the same minute.
    `gpt-4o-mini-tts` voiced the identical paragraph 3/3. Two live tests in
    tests/test_audio_functional.py were RED on exactly this.

    UNDO TEST: set DEFAULT_MODEL back to "tts-1-hd" -> RED here, and the live
    Occupation cases go red again.
    """

    def test_the_default_model_is_the_one_that_reads_the_occupation_beat(self):
        assert OpenAITTSProvider.DEFAULT_MODEL == "gpt-4o-mini-tts", (
            "tts-1-hd 500s on ordinary WWII history; it must not be the default"
        )

    def test_the_chunk_cap_stays_under_the_model_input_limit(self):
        """gpt-4o-mini-tts caps input at 2000 TOKENS (~8000 chars of English).

        4000 characters is roughly 1000 tokens — comfortably under, and also
        under the older model's 4096-character cap, so the one chunker serves
        both.
        """
        assert MAX_TTS_CHARS <= 4000


class TestFailoverChain:
    """A vendor's outage must cost a DIFFERENT VOICE, never silence.

    OpenAI answered an intermittent 500 ("The server had an error while
    processing your request") on 2026-08-28. ``_post_with_retry`` re-sends a 500
    three times over ~1.5 seconds, which rides out a blip and nothing longer, so
    with one pinned provider a minutes-long outage is simply no audio for a
    tourist. ``TTS_FALLBACK`` names the understudies; the whole chain is offline
    here — no key, no network, no cost.
    """

    @staticmethod
    def _speaks(provider_name: str, audio: bytes) -> tuple[type, list[dict]]:
        """A provider class that answers, plus the log of what it was asked."""
        calls: list[dict] = []

        class _Speaks:
            @property
            def name(self) -> str:
                return provider_name

            def generate(self, text: str, *, voice_id: str | None = None) -> bytes:
                calls.append({"text": text, "voice_id": voice_id})
                return audio

        return _Speaks, calls

    @staticmethod
    def _fails(provider_name: str, message: str) -> tuple[type, list[dict]]:
        """A provider class that raises TTSError, plus its call log."""
        calls: list[dict] = []

        class _Fails:
            @property
            def name(self) -> str:
                return provider_name

            def generate(self, text: str, *, voice_id: str | None = None) -> bytes:
                calls.append({"text": text, "voice_id": voice_id})
                raise TTSError(message)

        return _Fails, calls

    def _pin(self, monkeypatch, primary: type, understudy: type | None, fallback: str) -> None:
        monkeypatch.setitem(_PROVIDERS, "primary", primary)
        if understudy is not None:
            monkeypatch.setitem(_PROVIDERS, "understudy", understudy)
        monkeypatch.setenv("TTS_PROVIDER", "primary")
        monkeypatch.setenv("TTS_FALLBACK", fallback)

    def test_a_healthy_primary_speaks_and_the_understudy_is_never_called(self, monkeypatch):
        primary, primary_calls = self._speaks("primary", b"primary-audio")
        understudy, understudy_calls = self._speaks("understudy", b"understudy-audio")
        self._pin(monkeypatch, primary, understudy, "understudy")

        provider = get_provider_with_fallback()
        assert provider.generate("Hello Paris.") == b"primary-audio"
        assert provider.name == "primary"
        assert len(primary_calls) == 1
        assert understudy_calls == [], "a healthy primary must not spend on the understudy"

    def test_the_understudy_speaks_when_the_primary_fails(self, monkeypatch):
        primary, primary_calls = self._fails("primary", "500 server had an error")
        understudy, understudy_calls = self._speaks("understudy", b"understudy-audio")
        self._pin(monkeypatch, primary, understudy, "understudy")

        provider = get_provider_with_fallback()
        assert provider.generate("Hello Paris.") == b"understudy-audio"
        assert provider.name == "understudy", "the record must name who ACTUALLY spoke"
        assert len(primary_calls) == 1
        assert understudy_calls[0]["text"] == "Hello Paris.", "the WHOLE text is re-voiced"

    def test_every_provider_failing_names_each_one(self, monkeypatch):
        primary, _ = self._fails("primary", "500 server had an error")
        understudy, _ = self._fails("understudy", "ELEVENLABS_API_KEY not set")
        self._pin(monkeypatch, primary, understudy, "understudy")

        with pytest.raises(TTSError) as exc:
            get_provider_with_fallback().generate("Hello Paris.")
        assert "primary: 500 server had an error" in str(exc.value)
        assert "understudy: ELEVENLABS_API_KEY not set" in str(exc.value)

    def test_a_named_provider_is_honoured_exactly_and_never_chains(self, monkeypatch):
        """/audio/compare and /audio/eval ask "how does THIS voice sound".

        Answering with a different one would make the comparison a lie, so an
        explicit name resolves to that provider alone even with a chain pinned.
        """
        primary, _ = self._fails("primary", "500 server had an error")
        understudy, understudy_calls = self._speaks("understudy", b"understudy-audio")
        self._pin(monkeypatch, primary, understudy, "understudy")

        provider = get_provider_with_fallback("primary")
        assert not isinstance(provider, FailoverTTSProvider)
        with pytest.raises(TTSError, match="500 server had an error"):
            provider.generate("Hello Paris.")
        assert understudy_calls == []

    def test_no_fallback_configured_behaves_exactly_as_before(self, monkeypatch):
        """UNDO TEST: with TTS_FALLBACK unset nothing about today changes."""
        primary, _ = self._speaks("primary", b"primary-audio")
        monkeypatch.setitem(_PROVIDERS, "primary", primary)
        monkeypatch.setenv("TTS_PROVIDER", "primary")
        monkeypatch.delenv("TTS_FALLBACK", raising=False)

        provider = get_provider_with_fallback()
        assert not isinstance(provider, FailoverTTSProvider)
        assert provider.name == "primary"

    def test_an_unknown_understudy_raises_while_the_primary_is_still_healthy(self, monkeypatch):
        """FAIL LOUD. A typo'd understudy must not lie dormant until the outage."""
        primary, _ = self._speaks("primary", b"primary-audio")
        self._pin(monkeypatch, primary, None, "elevenlabz")

        with pytest.raises(ValueError, match="Unknown TTS provider 'elevenlabz'"):
            get_provider_with_fallback()

    def test_a_repeated_or_self_naming_fallback_cannot_double_the_outage_window(
        self, monkeypatch
    ):
        primary, primary_calls = self._fails("primary", "500 server had an error")
        understudy, _ = self._fails("understudy", "also down")
        self._pin(monkeypatch, primary, understudy, "primary, understudy, understudy")

        with pytest.raises(TTSError):
            get_provider_with_fallback().generate("Hello Paris.")
        assert len(primary_calls) == 1, "the primary is tried once, not once per mention"

    def test_a_chain_of_three_falls_through_in_order(self, monkeypatch):
        """The chain is N-deep, not two-deep — proven with three.

        Production ships two SERVER tiers (openai -> elevenlabs); the third tier
        is each surface's own OS voice and never resolves through this registry
        (a server-side local model was built and removed on 2026-08-29 — it
        cannot help a phone with no signal, which is the failure tier 3 exists
        for). The ordering logic still has to hold for any depth, so it is
        exercised here at three with a generic last resort.
        """
        primary, primary_calls = self._fails("openai", "500 server had an error")
        second, second_calls = self._fails("elevenlabs", "503 unavailable")
        third, third_calls = self._speaks("last-resort", b"local-audio")
        monkeypatch.setitem(_PROVIDERS, "openai", primary)
        monkeypatch.setitem(_PROVIDERS, "elevenlabs", second)
        monkeypatch.setitem(_PROVIDERS, "last-resort", third)
        monkeypatch.setenv("TTS_PROVIDER", "openai")
        monkeypatch.setenv("TTS_FALLBACK", "elevenlabs,last-resort")

        provider = get_provider_with_fallback()
        assert provider.generate("Hello Paris.") == b"local-audio"
        assert provider.name == "last-resort"
        assert len(primary_calls) == 1 and len(second_calls) == 1 and len(third_calls) == 1

    def test_the_voice_id_reaches_the_primary_only(self, monkeypatch):
        """A voice name belongs to ONE vendor.

        "nova" is OpenAI's; ElevenLabs wants its own id and would 404 on the
        other's. Each understudy uses its own configured default instead.
        """
        primary, primary_calls = self._fails("primary", "500 server had an error")
        understudy, understudy_calls = self._speaks("understudy", b"understudy-audio")
        self._pin(monkeypatch, primary, understudy, "understudy")

        get_provider_with_fallback().generate("Hello Paris.", voice_id="nova")
        assert primary_calls[0]["voice_id"] == "nova"
        assert understudy_calls[0]["voice_id"] is None


def test_audio_preview_request_accepts_long_narration():
    # A long stop (6000 chars) was rejected at the old 5000 cap (the 422 bug).
    AudioPreviewRequest(text="x" * 6000, provider="mock")  # must not raise


class TestTTSNormalizationWiring:
    """The real providers must normalize pronunciation BEFORE calling the API:
    the bytes the listener hears come from the normalized text.
    """

    def _capture_input(self, monkeypatch) -> list[str]:
        captured: list[str] = []

        def fake_post(self, *args, **kwargs):
            payload = kwargs["json"]
            captured.append(payload.get("input") or payload.get("text"))

            class _R:
                status_code = 200
                content = b"\xff\xfbaudio"

            return _R()

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        return captured

    def test_openai_normalizes_regnal_numerals(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        sent = self._capture_input(monkeypatch)

        OpenAITTSProvider().generate("Louis XIV met J.-B. Colbert under Napoleon III.")

        assert sent == ["Louis the fourteenth met J. B. Colbert under Napoleon the third."]

    def test_elevenlabs_normalizes_regnal_numerals(self, monkeypatch):
        monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
        monkeypatch.setenv("ELEVENLABS_VOICE_ID", "v")
        sent = self._capture_input(monkeypatch)

        ElevenLabsTTSProvider().generate("Charles V and Henri IV.")

        assert sent == ["Charles the fifth and Henri the fourth."]

    def test_non_regnal_text_reaches_api_unchanged(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        sent = self._capture_input(monkeypatch)

        text = "World War II shaped Chapter IV, whom I admire."
        OpenAITTSProvider().generate(text)

        assert sent == [text]
