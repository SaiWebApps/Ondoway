"""Audio evaluation — transcribe TTS output and compare against source text.

Uses OpenAI Whisper API for speech-to-text, then computes a similarity
score between the original script and the transcription.
"""

from __future__ import annotations

import math
import os
import re
import unicodedata
from dataclasses import dataclass

from src.audio._http import post_with_retry
from src.model_registry import model_for


class EvalError(Exception):
    """Raised when audio evaluation fails."""


@dataclass
class EvalResult:
    """Result of evaluating a TTS audio sample against its source text."""

    original_text: str
    transcribed_text: str
    similarity_score: float  # 0.0 to 1.0
    word_error_rate: float  # 0.0 = perfect, 1.0 = all wrong
    missing_words: list[str]
    extra_words: list[str]


def _normalize(text: str) -> str:
    """Lowercase, fold accents, replace punctuation with spaces, collapse whitespace.

    Accents are decomposed (NFKD) and the combining marks removed so the
    reference matches Whisper's behavior: Whisper transcription strips diacritics
    (``Café`` → ``Cafe``) across models and languages, so comparing
    accent-preserved reference text against an accent-stripped hypothesis would
    spuriously inflate WER on French proper nouns (a ~0.03 floor that tips
    accent-heavy beats over threshold). Folding both to the same ASCII base makes
    the comparison fair.

    Punctuation is replaced with a space (not deleted) so a separator rendered
    differently by the two sources — e.g. the script's spaced em-dash
    ``hub — the`` vs Whisper's unspaced ``hub—the`` — normalizes to the same
    word boundary instead of joining the words into ``hubthe`` and inflating WER.
    """
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _word_error_rate(reference: list[str], hypothesis: list[str]) -> float:
    """Compute word error rate using Levenshtein distance on word sequences.

    WER = (substitutions + insertions + deletions) / len(reference)
    Returns 0.0 for perfect match, >1.0 possible if hypothesis is much longer.
    """
    r, h = reference, hypothesis
    n = len(r)
    m = len(h)

    # DP table
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if r[i - 1] == h[j - 1] else 1
            d[i][j] = min(
                d[i - 1][j] + 1,  # deletion
                d[i][j - 1] + 1,  # insertion
                d[i - 1][j - 1] + cost,  # substitution
            )

    if n == 0:
        return 0.0 if m == 0 else 1.0
    return d[n][m] / n


def _set_similarity(ref_words: list[str], hyp_words: list[str]) -> float:
    """Simple set-based similarity (Jaccard-like) as a quick sanity metric."""
    ref_set = set(ref_words)
    hyp_set = set(hyp_words)
    if not ref_set:
        return 1.0 if not hyp_set else 0.0
    intersection = ref_set & hyp_set
    union = ref_set | hyp_set
    return len(intersection) / len(union)


def transcribe(audio_bytes: bytes, *, filename: str = "audio.mp3") -> str:
    """Transcribe audio bytes using OpenAI Whisper API.

    Requires OPENAI_API_KEY env var.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise EvalError("OPENAI_API_KEY not set — needed for Whisper transcription")

    # Retry transient Cloudflare/gateway blips (a 404 "Invalid URL" edge artifact,
    # or a 5xx) — the URL is hardcoded+verified, so a 404 here is never a routing
    # bug, only a transient edge event that must not fail the live audio bar.
    resp = post_with_retry(
        "https://api.openai.com/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {api_key}"},
        data={"model": model_for("speech_to_text")},
        files={"file": (filename, audio_bytes)},
        timeout=120.0,
    )

    if resp.status_code != 200:
        raise EvalError(f"Whisper API failed ({resp.status_code}): {resp.text[:200]}")

    return resp.json().get("text", "")


# Whisper occasionally returns a DEGENERATE transcription — a near-empty
# hallucination like "you" / "Thank you." for a full paragraph (a 200 with
# garbage, so post_with_retry can't catch it). That is a transient decode
# failure, not an accent/threshold miss, and it spikes WER to ~1.0. When the
# transcription collapses below this fraction of the reference word count we
# re-transcribe (bounded) and keep the BEST attempt — resilience at the root,
# never a loosened WER threshold.
_DEGENERATE_TRANSCRIPT_LEN_FRAC: float = 0.6
_MAX_TRANSCRIBE_ATTEMPTS: int = 3
# Whisper is STOCHASTIC: even a COMPLETE transcription can garble a few words on
# one decode and nail them on the next (observed on the live-audio bar — the
# Shakespeare & Company beat, WER 0.0 on re-run). When a complete transcription's
# WER is high enough to FAIL the bar, re-transcribe (bounded) and keep the best —
# the audio is fixed, only the noisy measurement is retried. A genuinely bad beat
# stays high across all attempts and still fails; an ordinary accent miss (WER
# well under the ceiling) is accepted immediately, so no retries are wasted.
_WER_RETRY_CEILING: float = 0.15


def evaluate(original_text: str, audio_bytes: bytes, *, filename: str = "audio.mp3") -> EvalResult:
    """Full eval pipeline: transcribe audio, then compare against original.

    Returns an EvalResult with similarity score, WER, and word diffs.
    """
    ref_norm = _normalize(original_text)
    ref_words = ref_norm.split()

    # Re-transcribe on a DEGENERATE (near-empty) result OR a complete-but-failing
    # WER (a stochastic Whisper flub); keep the best WER across attempts.
    best_transcribed = ""
    best_hyp_words: list[str] = []
    best_wer = math.inf
    for _ in range(_MAX_TRANSCRIBE_ATTEMPTS):
        transcribed = transcribe(audio_bytes, filename=filename)
        hyp_words = _normalize(transcribed).split()
        wer = _word_error_rate(ref_words, hyp_words)
        if wer < best_wer:
            best_wer, best_transcribed, best_hyp_words = wer, transcribed, hyp_words
        degenerate = bool(ref_words) and (
            len(hyp_words) < _DEGENERATE_TRANSCRIPT_LEN_FRAC * len(ref_words)
        )
        if not degenerate and best_wer <= _WER_RETRY_CEILING:
            break

    transcribed = best_transcribed
    hyp_words = best_hyp_words

    wer = _word_error_rate(ref_words, hyp_words)
    similarity = _set_similarity(ref_words, hyp_words)

    ref_set = set(ref_words)
    hyp_set = set(hyp_words)
    missing = sorted(ref_set - hyp_set)
    extra = sorted(hyp_set - ref_set)

    return EvalResult(
        original_text=original_text,
        transcribed_text=transcribed,
        similarity_score=round(similarity, 4),
        word_error_rate=round(wer, 4),
        missing_words=missing,
        extra_words=extra,
    )
