"""Audio generation routes — TTS preview, provider listing, and comparison."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from neo4j import Session

from src.api.auth.dependencies import get_current_user
from src.api.dependencies import get_session
from src.api.models.audio import (
    AudioPreviewRequest,
    CompareRequest,
    CompareResponse,
    CompareResultItem,
    EvalRequest,
    EvalResponse,
    GenerateRequest,
    KeepExploringAudioResponse,
    ProviderInfo,
    ProviderListResponse,
    StopAudioResultItem,
    StopAudioStatusResponse,
    TripStopAudioGenerateResponse,
)
from src.audio.eval import EvalError as AudioEvalError
from src.audio.eval import evaluate
from src.audio.pipeline import PipelineError, generate_stop_audio
from src.audio.provider import TTSError, get_provider, list_providers
from src.audio.storage import LocalStorageProvider, get_storage

router = APIRouter(tags=["audio"])

# ── Admin gate for the spend/mutate audio surface ──────────────────────────
#
# The audio router is mounted UNCONDITIONALLY (mobile + the editorial workbench
# need the playback/preview routes), but several of its routes are an
# unauthenticated *spend* surface:
#
#   POST /audio/compare         -> up to 5 paid TTS calls + writes files to disk
#   GET  /audio/compare/download-> serves those files
#   POST /audio/eval            -> up to 3 paid TTS + 9 paid Whisper calls
#
# (The per-beat library — POST /audio/generate-batch, /audio/generate/{id},
# /audio/generate-trip, GET /audio/status/{id} — was DELETED at Phase 7 S7.10,
# design §8.5: the per-stop path and the keep-exploring door are the only voicing
# doors.) None of these has a product caller (mobile uses generate-trip-stops /
# keep-exploring; the web preview uses /audio/preview) — they are operator tools. Gate
# them behind the SAME fail-closed flag that already protects the workbench CRUD
# routers in src/api/app.py, so the public Render deployment (which pins
# WORKBENCH_API_ENABLED="false") never exposes them.
#
# The check runs per-REQUEST rather than at mount time because this module is
# imported once but ``create_app()`` may be called many times (tests) with a
# different env; a request-time gate can never be stale. A disabled route
# answers 404 so it is indistinguishable from an unmounted one.
_ADMIN_GATE_ENV = "WORKBENCH_API_ENABLED"


def _audio_admin_enabled_value(value: str | None) -> bool:
    """Fail-closed parse of the admin gate (mirrors app._workbench_api_enabled).

    ONLY an explicit truthy value opens the surface; unset, empty, or a typo
    ("flase", "disbaled") keeps it CLOSED. Split out from the env lookup so a
    manifest test can check what render.yaml pins with the SAME parser the
    runtime uses. Defined locally rather than imported from src.api.app to
    avoid a circular import (app imports this module).
    """
    return (value or "").strip().lower() in ("true", "1", "yes", "on")


def _audio_admin_enabled() -> bool:
    """Whether the audio spend/mutate surface is explicitly enabled."""
    return _audio_admin_enabled_value(os.getenv(_ADMIN_GATE_ENV))


def require_audio_admin() -> None:
    """Dependency: 404 unless the audio admin surface is explicitly enabled."""
    if not _audio_admin_enabled():
        raise HTTPException(404, "Not Found")


# RATE LIMITING REMOVED 2026-07-31 (owner order, twice stated). The audio routes
# used to carry a per-IP + global fixed-window guard. It is deleted, not disabled:
# no constants, no counters, no env knobs, nothing to re-enable by accident.
#
# What that means, stated once: /audio/preview is anonymous, so nothing now
# bounds how much an unauthenticated caller can spend against the configured TTS
# key. If a bound is wanted later it should key on the AUTHENTICATED user, not
# the client IP — mobile carriers put thousands of users behind one address, so
# the old per-IP cap throttled real tourists in groups while barely slowing a
# determined caller.

# Bounded in-memory cache of preview audio keyed by (provider, voice, text) so a
# replayed identical payload is never re-billed. Mirrors the narration_hash
# cache used by keep-exploring below.
_PREVIEW_CACHE_MAX_ENTRIES = int(os.getenv("AUDIO_PREVIEW_CACHE_ENTRIES", "16"))
_preview_cache: OrderedDict[str, bytes] = OrderedDict()
_preview_cache_lock = Lock()

# Directory for storing comparison audio files (survives across requests)
_COMPARE_DIR = Path(tempfile.gettempdir()) / "ondoway-audio-compare"
_COMPARE_DIR.mkdir(exist_ok=True)

# Maximum age (in seconds) for comparison files before cleanup
_COMPARE_MAX_AGE_SEC = 3600  # 1 hour
# Hard bounds on the comparison cache. Age-only cleanup let a caller fill the
# container's ephemeral disk within the retention window, breaking every other
# write the process needs (including audio storage).
_COMPARE_MAX_TOTAL_BYTES = int(os.getenv("AUDIO_COMPARE_MAX_TOTAL_BYTES", str(200 * 1024 * 1024)))
_COMPARE_MAX_FILES = int(os.getenv("AUDIO_COMPARE_MAX_FILES", "100"))

# Local audio URLs produced by LocalStorageProvider.upload().
_LOCAL_AUDIO_URL_PREFIX = "/api/v1/audio/files/"


def _preview_cache_get(key: str) -> bytes | None:
    with _preview_cache_lock:
        hit = _preview_cache.get(key)
        if hit is not None:
            _preview_cache.move_to_end(key)
        return hit


def _preview_cache_put(key: str, data: bytes) -> None:
    if _PREVIEW_CACHE_MAX_ENTRIES <= 0:
        return
    with _preview_cache_lock:
        _preview_cache[key] = data
        _preview_cache.move_to_end(key)
        while len(_preview_cache) > _PREVIEW_CACHE_MAX_ENTRIES:
            _preview_cache.popitem(last=False)


def _artifact_missing(url: str | None) -> bool:
    """Whether a stored audio URL points at an artifact that no longer exists.

    Prod keeps the durable pointer in Neo4j but (with AUDIO_STORAGE=local) the
    bytes live on the container's ephemeral disk, so a redeploy leaves rows whose
    audio_url 404s forever — and every regeneration guard treats "has a url" as
    "has audio", so nothing self-heals. Treat a missing artifact as no-audio.

    Conservative by construction: returns False for anything we cannot verify
    (remote storage, unparseable URL, probe error), so this can never cause a
    spurious regeneration.
    """
    if not url or not url.startswith(_LOCAL_AUDIO_URL_PREFIX):
        return False
    key = url[len(_LOCAL_AUDIO_URL_PREFIX) :]
    if not key:
        return False
    try:
        storage = get_storage()
        if not isinstance(storage, LocalStorageProvider):
            return False
        return not storage.exists(key)
    except Exception:
        return False


def _stop_narration_hash(
    narration: str, provider_name: str | None, voice_id: str | None
) -> str:
    """Content key for a stop's tour audio: (provider, voice, narration).

    The same digest input the keep-exploring path uses below, so the two
    staleness guards on ItineraryItem cannot drift apart. Provider and voice are
    part of the key because both are per-request overrides: without them a caller
    asking for a different voice would silently be handed audio in the old one.
    """
    return hashlib.sha256(
        f"{provider_name or ''}\x00{voice_id or ''}\x00{narration}".encode()
    ).hexdigest()


# Max chars of narration handed to TTS for the on-demand keep-exploring deep
# dive. Mirrors AudioPreviewRequest's cap so a huge extra_narration can't fan
# out into unbounded TTS chunk requests (Defect 17).
_KEEP_EXPLORING_MAX_CHARS = 20000


def _cap_narration(text: str, max_chars: int = _KEEP_EXPLORING_MAX_CHARS) -> str:
    """Cap narration to ``max_chars``, preferring a sentence boundary.

    Text at or under the cap is returned unchanged. Over the cap, truncate to
    the last sentence-ender (``.``/``!``/``?``) within the window so the voiced
    deep dive still ends cleanly; if none exists, hard-truncate at the cap.
    """
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    cut = max(window.rfind("."), window.rfind("!"), window.rfind("?"))
    if cut > 0:
        return window[: cut + 1]
    return window


def _cleanup_old_comparisons() -> None:
    """Remove comparison files older than _COMPARE_MAX_AGE_SEC."""
    try:
        now = time.time()
        for entry in _COMPARE_DIR.iterdir():
            if entry.is_file():
                try:
                    age = now - os.path.getmtime(entry)
                    if age > _COMPARE_MAX_AGE_SEC:
                        entry.unlink()
                except OSError:
                    pass  # File may have been removed concurrently
    except OSError:
        pass  # Directory may not exist yet


def _enforce_compare_cache_bounds() -> None:
    """Evict oldest comparison files until BOTH the byte and count caps hold.

    Age-based cleanup alone bounds nothing within the retention window: a caller
    could write unbounded bytes for a full hour and exhaust the container's
    ephemeral disk. Run this AFTER every write so the cache is bounded at all
    times regardless of request rate.
    """
    try:
        entries = []
        for entry in _COMPARE_DIR.iterdir():
            if not entry.is_file():
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime, stat.st_size, entry))
    except OSError:
        return

    entries.sort(key=lambda e: e[0])  # oldest first
    total_bytes = sum(e[1] for e in entries)
    count = len(entries)
    for _mtime, size, entry in entries:
        if total_bytes <= _COMPARE_MAX_TOTAL_BYTES and count <= _COMPARE_MAX_FILES:
            break
        try:
            entry.unlink()
        except OSError:
            continue
        total_bytes -= size
        count -= 1


def _provider_available(name: str) -> bool:
    """Report whether a provider is actually usable, not merely instantiable.

    The real providers have no ``__init__`` and defer their readiness checks to
    ``generate()``, so instantiation never raises even when nothing is
    configured. Probe what each one actually needs so a client can distinguish a
    usable provider from one that will 502 on first generate. Unknown providers
    fall back to instantiation success.

    Both registered providers are vendors and need credentials. The chain's
    third tier is not a provider here — it is each surface's own OS voice, so
    it has nothing for this probe to report.
    """
    try:
        get_provider(name)
    except Exception:
        return False
    if name == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    if name == "elevenlabs":
        return bool(os.getenv("ELEVENLABS_API_KEY")) and bool(os.getenv("ELEVENLABS_VOICE_ID"))
    return True


@router.get("/audio/providers", response_model=ProviderListResponse)
def get_providers():
    """List all registered TTS providers and their availability."""
    providers = [
        ProviderInfo(name=name, available=_provider_available(name)) for name in list_providers()
    ]
    return ProviderListResponse(providers=providers)


@router.post("/audio/preview")
def preview_audio(body: AudioPreviewRequest, request: Request):
    """Generate a TTS audio preview from raw text. Returns audio/mpeg bytes.

    This route is deliberately anonymous — the editorial workbench calls it with
    no credentials. Its rate limit was DELETED on 2026-07-31 by owner order,
    and its text cap was deleted on 2026-08-04 by owner order so the workbench
    judges the whole narration rather than a truncation. The only thing now
    bounding what an anonymous caller can spend is the content-hash cache that
    stops a replayed payload being re-billed, plus the request model's own
    20000-character ceiling. A real bound belongs on the AUTHENTICATED user; the
    workbench moves onto that path in Phase 2. This docstring ships in the
    OpenAPI schema; keep it honest.
    """
    try:
        provider = get_provider(body.provider)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    text = body.text

    cache_key = hashlib.sha256(
        f"{provider.name}\x00{body.voice_id or ''}\x00{text}".encode()
    ).hexdigest()
    audio_bytes = _preview_cache_get(cache_key)
    if audio_bytes is None:
        try:
            audio_bytes = provider.generate(text, voice_id=body.voice_id)
        except TTSError as e:
            raise HTTPException(502, f"TTS generation failed ({provider.name}): {e}") from None
        _preview_cache_put(cache_key, audio_bytes)

    # Every registered provider sends text to a real speech service and returns MP3.
    media_type = "audio/mpeg"
    ext = "mp3"

    return Response(
        content=audio_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="preview-{provider.name}.{ext}"'},
    )


@router.post(
    "/audio/compare",
    response_model=CompareResponse,
    dependencies=[Depends(require_audio_admin)],
)
def compare_providers(body: CompareRequest, request: Request):
    """Generate the same text with multiple providers for A/B comparison.

    Returns metadata and download paths for each provider's output.
    Use GET /audio/compare/download/{filename} to fetch individual files.

    Operator/workbench tool: gated by require_audio_admin (404 on the public
    deployment), rate-limited, and bounded on disk.
    """
    # Clean up stale comparison files (older than 1 hour)
    _cleanup_old_comparisons()

    # Create a hash for this comparison batch
    text_hash = hashlib.sha256(body.text.encode()).hexdigest()[:12]
    results: list[CompareResultItem] = []

    # The model caps the list at 5 but does NOT require uniqueness, so
    # ["openai"] * 5 multiplied the bill 5x for one text. Dedupe, preserving
    # the caller's order.
    for provider_name in dict.fromkeys(body.providers):
        try:
            provider = get_provider(provider_name)
        except ValueError as e:
            results.append(
                CompareResultItem(
                    provider=provider_name,
                    success=False,
                    error=str(e),
                )
            )
            continue

        try:
            voice_id = body.voice_ids.get(provider_name)
            audio_bytes = provider.generate(body.text, voice_id=voice_id)
        except TTSError as e:
            results.append(
                CompareResultItem(
                    provider=provider_name,
                    success=False,
                    error=str(e),
                )
            )
            continue

        ext = "mp3"
        filename = f"{text_hash}-{provider_name}.{ext}"
        filepath = _COMPARE_DIR / filename
        filepath.write_bytes(audio_bytes)
        _enforce_compare_cache_bounds()

        # Estimate duration from word count (~150 wpm)
        word_count = len(body.text.split())
        duration_est = word_count / 2.5

        results.append(
            CompareResultItem(
                provider=provider_name,
                success=True,
                size_bytes=len(audio_bytes),
                duration_estimate_sec=round(duration_est, 1),
                download_path=f"/api/v1/audio/compare/download/{filename}",
            )
        )

    return CompareResponse(text=body.text, results=results)


@router.get(
    "/audio/compare/download/{filename}",
    dependencies=[Depends(require_audio_admin)],
)
def download_comparison(filename: str):
    """Download a previously generated comparison audio file."""
    # Sanitize filename to prevent path traversal. Path(...).name does NOT strip
    # a bare ".." or "." (both are their own `name`), so the old exists() check
    # passed for a DIRECTORY and FileResponse then raised RuntimeError -> HTTP
    # 500 with the server's temp path in the trace. is_file() rejects both, and
    # the containment check is defense in depth.
    safe_name = Path(filename).name
    filepath = (_COMPARE_DIR / safe_name).resolve()
    if not filepath.is_file() or filepath.parent != _COMPARE_DIR.resolve():
        raise HTTPException(404, f"File '{safe_name}' not found. Run /audio/compare first.")

    media_type = "audio/wav" if safe_name.endswith(".wav") else "audio/mpeg"
    return FileResponse(filepath, media_type=media_type, filename=safe_name)


# A real TTS occasionally returns a 200 with degraded/near-silent audio (Whisper
# transcribes it as "you", WER ~1.0) -- transient output degradation, NOT a real
# quality regression. Regenerate a bounded number of times and keep the best
# (lowest-WER) result so a one-off bad generation can't fail an otherwise-good
# voice. WER this high never comes from a legitimate script, so this cannot mask
# a real quality issue (a genuinely bad voice fails every attempt and still
# surfaces). The deterministic mock provider's silent WAV is "degraded" by
# design, so it is evaluated once (retrying it would loop pointlessly).
_EVAL_DEGRADED_WER: float = 0.6  # far above the FAIL cut (0.25) -- only garbage output
_EVAL_MAX_ATTEMPTS: int = 3


@router.post(
    "/audio/eval",
    response_model=EvalResponse,
    dependencies=[Depends(require_audio_admin)],
)
def eval_audio(body: EvalRequest, request: Request):
    """Generate TTS audio, then transcribe it back and compare against the source.

    This is the end-to-end quality check: text → TTS → audio → Whisper STT → diff.
    Requires OPENAI_API_KEY for Whisper transcription.

    Verdict thresholds:
    - PASS: WER < 0.10 (less than 10% word errors)
    - REVIEW: WER 0.10-0.25
    - FAIL: WER > 0.25
    """
    try:
        provider = get_provider(body.provider)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    # One eval request costs up to 3 paid TTS syntheses AND 3 paid Whisper
    # transcriptions, so it is limited even though it is admin-gated.

    # Steps 1-2: Generate audio then transcribe + evaluate, retrying past a
    # transient degraded generation (see _EVAL_DEGRADED_WER above).
    ext = "mp3"
    max_attempts = _EVAL_MAX_ATTEMPTS
    result = None
    for _ in range(max_attempts):
        # A transient failure on a LATER attempt must not discard a good result
        # already captured on an earlier one: only surface a 502 while we still
        # have no successful attempt (result is None). Otherwise stop retrying
        # and fall through to verdict assignment using the best result so far.
        try:
            audio_bytes = provider.generate(body.text, voice_id=body.voice_id)
        except TTSError as e:
            if result is not None:
                break
            raise HTTPException(502, f"TTS generation failed ({provider.name}): {e}") from None
        try:
            attempt = evaluate(body.text, audio_bytes, filename=f"eval.{ext}")
        except AudioEvalError as e:
            if result is not None:
                break
            raise HTTPException(502, f"Transcription failed: {e}") from None
        if result is None or attempt.word_error_rate < result.word_error_rate:
            result = attempt
        if result.word_error_rate < _EVAL_DEGRADED_WER:
            break  # a plausible (non-degraded) generation -- stop retrying

    # Step 3: Assign verdict
    if result.word_error_rate < 0.10:
        verdict = "PASS"
    elif result.word_error_rate < 0.25:
        verdict = "REVIEW"
    else:
        verdict = "FAIL"

    return EvalResponse(
        provider=provider.name,
        original_text=result.original_text,
        transcribed_text=result.transcribed_text,
        similarity_score=result.similarity_score,
        word_error_rate=result.word_error_rate,
        missing_words=result.missing_words,
        extra_words=result.extra_words,
        verdict=verdict,
    )


@router.get("/audio/files/{key:path}")
def serve_audio_file(key: str):
    """Serve a locally stored audio file.

    Only works when AUDIO_STORAGE=local (default for development).
    In production, audio is served directly from S3/CDN.
    """
    storage = get_storage()
    if not isinstance(storage, LocalStorageProvider):
        raise HTTPException(404, "Local file serving only available with local storage")

    filepath = storage.base_path / key
    # Prevent path traversal
    try:
        filepath.resolve().relative_to(storage.base_path.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid file path") from None

    # is_file(), not exists(): an empty key resolves to the storage ROOT and any
    # directory under it also "exists", so exists() let a directory through to
    # FileResponse, which raised RuntimeError -> HTTP 500 leaking the absolute
    # server-side storage path. is_file() routes both cases into this 404.
    if not filepath.is_file():
        raise HTTPException(404, f"Audio file '{key}' not found")

    media_type = "audio/wav" if key.endswith(".wav") else "audio/mpeg"
    return FileResponse(filepath, media_type=media_type, filename=Path(key).name)


# ── Pipeline endpoints (require Neo4j) — the PER-STOP path ──


def _voice_session_lines(
    session,
    stop: dict,
    *,
    provider_name: str | None,
    voice_id: str | None,
    force: bool,
) -> None:
    """Voice one stop's authored session lines (Phase 6 S6.8) — see the caller."""
    from src.tour.degradations import record

    stop_id = stop["stop_id"]

    def _voice_one(text: str, key: str) -> tuple[str, float] | None:
        try:
            gen = generate_stop_audio(
                text,
                stop_key=key,
                poi_name=stop.get("poi_name"),
                provider_name=provider_name,
                voice_id=voice_id,
            )
            return gen.audio_url, gen.duration_sec
        except PipelineError as exc:
            record(
                kind="session_line_not_voiced",
                human=(
                    "One short spoken line (a stop's goodbye or bridge line) could "
                    "not be recorded; the phone will say it in the plain voice."
                ),
                component="audio._voice_session_lines",
                cause=f"{key}: {exc}",
                stop_index=stop_id,
            )
            return None

    # THE ONE TABLE of a stop's small authored pieces, each voiced once and
    # hash-guarded: the close, the full telling's close, and — Phase 7 S7.7 (design
    # §5.6 C7; plan defect 7) — the LEG piece, the walking line into the stop, played
    # on the leg. The leg piece also stores its measured length (the phone's clocks
    # read it); the closes need none.
    for text_field, url_field, hash_field, key_suffix, duration_field in (
        ("close_text", "close_audio_url", "close_audio_hash", "close", None),
        ("full_close_text", "full_close_audio_url", "full_close_audio_hash", "full-close", None),
        ("leg_narration", "leg_audio_url", "leg_audio_hash", "leg", "leg_audio_duration_sec"),
    ):
        text = stop.get(text_field)
        if not text or not str(text).strip():
            continue
        line_hash = _stop_narration_hash(text, provider_name, voice_id)
        if (
            stop.get(url_field)
            and stop.get(hash_field) == line_hash
            and not force
            and not _artifact_missing(stop[url_field])
        ):
            continue
        voiced = _voice_one(text, f"{stop_id}-{key_suffix}")
        if voiced is not None:
            url, duration = voiced
            duration_set = f", i.{duration_field} = $dur" if duration_field else ""
            session.run(
                f"MATCH (i:ItineraryItem {{id: $sid}}) "
                f"SET i.{url_field} = $url, i.{hash_field} = $hash{duration_set}",
                sid=stop_id,
                url=url,
                hash=line_hash,
                dur=duration,
            )

    raw_threads = stop.get("thread_lines")
    if raw_threads:
        try:
            threads = json.loads(raw_threads)
        except (TypeError, ValueError):
            threads = {}
        if threads:
            combined_hash = _stop_narration_hash(
                json.dumps(threads, sort_keys=True, ensure_ascii=False),
                provider_name,
                voice_id,
            )
            if not (
                stop.get("thread_audio_urls")
                and stop.get("thread_audio_hash") == combined_hash
                and not force
            ):
                urls: dict[str, str] = {}
                for from_name, text in threads.items():
                    voiced = _voice_one(text, f"{stop_id}-thread-{abs(hash(from_name)) % 10**8}")
                    if voiced is not None:
                        urls[from_name] = voiced[0]
                if urls:
                    session.run(
                        "MATCH (i:ItineraryItem {id: $sid}) "
                        "SET i.thread_audio_urls = $urls, i.thread_audio_hash = $hash",
                        sid=stop_id,
                        urls=json.dumps(urls, ensure_ascii=False),
                        hash=combined_hash,
                    )

    # Phase 7 S7.7 (B) (design §5.6 "segments"; W7.2 R4): a marquee's CHAPTERS — each
    # voiced once as its own file, `{stop}-seg-{i}`, hash-guarded like the lines above;
    # the url, the measured length and the hash are written back INTO the item's
    # chapter list (the thread-urls precedent), which the session GET overlays.
    raw_segments = stop.get("segments_json")
    if raw_segments:
        try:
            segments = json.loads(raw_segments)
        except (TypeError, ValueError):
            segments = []
        changed = False
        for i, seg in enumerate(segments if isinstance(segments, list) else []):
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("narration") or "").strip()
            if not text:
                continue
            seg_hash = _stop_narration_hash(text, provider_name, voice_id)
            if (
                seg.get("audio_url")
                and seg.get("audio_hash") == seg_hash
                and not force
                and not _artifact_missing(seg["audio_url"])
            ):
                continue
            voiced = _voice_one(text, f"{stop_id}-seg-{i}")
            if voiced is None:
                continue
            seg["audio_url"], seg["audio_duration_sec"] = voiced
            seg["audio_hash"] = seg_hash
            changed = True
        if changed:
            session.run(
                "MATCH (i:ItineraryItem {id: $sid}) SET i.segments_json = $segments",
                sid=stop_id,
                segments=json.dumps(segments, ensure_ascii=False),
            )


@router.post(
    "/audio/generate-trip-stops/{trip_id}",
    response_model=TripStopAudioGenerateResponse,
)
def generate_stop_audio_for_trip(
    trip_id: str,
    body: GenerateRequest | None = None,
    current_user: dict = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Generate one composed-narration MP3 per stop (Phase 1, Step 1.4a).

    Iterates the trip's ItineraryItems, voices each stop's stitched ``narration``
    (Step 1.2) via ``generate_stop_audio``, and stores the result ON THE ITEM
    (``audio_url``/``audio_duration_sec``), keyed by the item id. THE voicing door
    for a trip (the per-beat ``/audio/generate-trip`` was deleted at Phase 7 S7.10).
    Items with no narration are skipped; existing audio is skipped unless force.

    READER-SCOPED: voicing spends real provider money per stop, so the caller
    must be an authenticated captain-or-crew reader of the trip — the same
    resolution the session GET uses. A stranger's guess and a trip that does
    not exist are the same 404 (the no-confirmation rule).
    """
    # Imported at call time: routes/trips.py lazily imports this module
    # (_revoice_replanned_legs), so a module-level import back would be the
    # circular pair's second half.
    from src.api.routes.trips import _readable_trip_or_404

    _readable_trip_or_404(session, current_user["id"], trip_id)

    rows = session.run(
        """
        MATCH (t:Trip {id: $trip_id})-[:HAS_STOP]->(item:ItineraryItem)
        OPTIONAL MATCH (item)-[:AT_POI]->(poi:POI)
        RETURN item.id AS stop_id,
               item.narration AS narration,
               item.audio_url AS audio_url,
               item.audio_script_hash AS audio_script_hash,
               item.close_text AS close_text,
               item.close_audio_url AS close_audio_url,
               item.close_audio_hash AS close_audio_hash,
               item.full_close_text AS full_close_text,
               item.full_close_audio_url AS full_close_audio_url,
               item.full_close_audio_hash AS full_close_audio_hash,
               item.thread_lines AS thread_lines,
               item.thread_audio_urls AS thread_audio_urls,
               item.thread_audio_hash AS thread_audio_hash,
               item.leg_narration AS leg_narration,
               item.leg_audio_url AS leg_audio_url,
               item.leg_audio_hash AS leg_audio_hash,
               item.segments_json AS segments_json,
               poi.name AS poi_name
        ORDER BY item.sort_order
        """,
        trip_id=trip_id,
    )
    stops = [dict(r) for r in rows]

    provider_name = body.provider if body else None
    voice_id = body.voice_id if body else None
    force = body.force if body else False

    results: list[StopAudioResultItem] = []
    for stop in stops:
        stop_id = stop["stop_id"]
        narration = stop["narration"]
        if not narration or not narration.strip():
            results.append(
                StopAudioResultItem(stop_id=stop_id, status="skipped", reason="no narration")
            )
            continue
        narration_hash = _stop_narration_hash(narration, provider_name, voice_id)
        # A skip needs BOTH halves to hold, and either one alone is a known bug:
        #   - url without hash: edit a stop's narration and the stale audio
        #     survives forever, which is the defect this guard closes.
        #   - hash without url: nothing has been voiced yet, so there is nothing
        #     to skip.
        # "Has a url" is also not "has audio": with AUDIO_STORAGE=local the bytes
        # sit on the container's ephemeral disk while the url persists in Neo4j,
        # so after a redeploy every stop would skip here forever and the tour
        # plays silence. Treat a verifiably-missing artifact as no-audio so a
        # plain (non-force) regeneration self-heals.
        if (
            stop["audio_url"]
            and stop["audio_script_hash"] == narration_hash
            and not force
            and not _artifact_missing(stop["audio_url"])
        ):
            results.append(
                StopAudioResultItem(
                    stop_id=stop_id,
                    status="skipped",
                    reason="already has audio",
                    audio_url=stop["audio_url"],
                )
            )
            continue
        try:
            gen = generate_stop_audio(
                narration,
                stop_key=stop_id,
                poi_name=stop["poi_name"],
                provider_name=provider_name,
                voice_id=voice_id,
            )
        except PipelineError as e:
            results.append(StopAudioResultItem(stop_id=stop_id, status="failed", error=str(e)))
            continue
        # audio_provider records WHO ACTUALLY SPOKE, which is not always who was
        # asked: an unnamed provider resolves to the TTS_FALLBACK chain, so a
        # vendor outage mid-pass leaves some stops in the understudy's voice.
        # The hash is keyed on the REQUESTED provider (unchanged), so those stops
        # are not re-billed when the primary recovers — they stay in the second
        # voice until a force pass. Without this column nothing durable says
        # which ones those are.
        session.run(
            "MATCH (item:ItineraryItem {id: $sid}) "
            "SET item.audio_url = $url, item.audio_duration_sec = $dur, "
            "    item.audio_script_hash = $hash, item.audio_provider = $provider",
            sid=stop_id,
            url=gen.audio_url,
            dur=gen.duration_sec,
            hash=narration_hash,
            provider=gen.provider,
        )
        results.append(
            StopAudioResultItem(stop_id=stop_id, status="generated", audio_url=gen.audio_url)
        )

    # Phase 6 S6.8 (W6.2 R6a, 11/0; owner ruling 2026-08-19: the tour's own voice,
    # never the robot). The AUTHORED SESSION LINES ride the same voicing pass as
    # their own small artifacts — each stop's close, its thread lines, the full
    # telling's close — hash-guarded exactly like the narration, so a line is
    # billed once. The contingency set's fixed lines ("Next: X") stay SCREEN-ONLY
    # (R6, 8/11) and are never sent here. A failed line leaves its url null (the
    # phone falls back to the plain spoken line) and is recorded, never silent.
    for stop in stops:
        _voice_session_lines(
            session,
            stop,
            provider_name=provider_name,
            voice_id=voice_id,
            force=force,
        )

    return TripStopAudioGenerateResponse(
        trip_id=trip_id,
        generated=sum(1 for r in results if r.status == "generated"),
        skipped=sum(1 for r in results if r.status == "skipped"),
        failed=sum(1 for r in results if r.status == "failed"),
        results=results,
    )


@router.get("/audio/stop-status/{stop_id}", response_model=StopAudioStatusResponse)
def get_stop_audio_status(stop_id: str, session: Session = Depends(get_session)):
    """Per-stop audio status by ItineraryItem id (Phase 1, Step 1.4b).

    Reads the per-stop audio persisted by /audio/generate-trip-stops (Step 1.4a) so
    mobile can poll/play per stop — the ONLY status poll since Phase 7 S7.10 deleted
    the per-beat one. 404 if the stop doesn't exist.
    """
    rec = session.run(
        "MATCH (i:ItineraryItem {id: $sid}) "
        "RETURN i.audio_url AS audio_url, i.audio_duration_sec AS duration_sec",
        sid=stop_id,
    ).single()
    if rec is None:
        raise HTTPException(404, f"Stop '{stop_id}' not found")

    audio_url = rec["audio_url"]
    has_audio = bool(audio_url)
    return StopAudioStatusResponse(
        stop_id=stop_id,
        has_audio=has_audio,
        audio_url=audio_url if has_audio else None,
        duration_sec=rec["duration_sec"] if has_audio else None,
    )


@router.post(
    "/audio/stops/{stop_id}/keep-exploring",
    response_model=KeepExploringAudioResponse,
)
def keep_exploring_stop_audio(
    stop_id: str,
    request: Request,
    body: GenerateRequest | None = None,
    session: Session = Depends(get_session),
):
    """KE3: voice a stop's "keep exploring here" extra narration on demand.

    Reads the ItineraryItem's persisted ``extra_narration`` (the deterministic
    stitch of the stop's overflow corpus beats, composed at /compose) and voices
    it via the SAME TTS path the tour audio uses (``generate_stop_audio``). This
    is an ON-DEMAND deep dive served OFF the tour's time budget, so nothing is
    auto-advanced and no itinerary time is accounted (that is a mobile concern).

    - 404 if the stop doesn't exist.
    - 409 if the stop has no extra_narration (nothing more to explore here).
    - On TTS failure, returns 200 with ``status='failed'`` — mirroring
      /audio/generate-trip-stops, this never raises a 500 for a flaky provider.

    The audio is NOT persisted on the item's ``audio_url`` (that field holds the
    scheduled per-stop tour audio); the fresh artifact url is returned for the
    client to play directly.
    """
    rec = session.run(
        "MATCH (i:ItineraryItem {id: $sid}) "
        "OPTIONAL MATCH (i)-[:AT_POI]->(poi:POI) "
        "RETURN i.extra_narration AS extra_narration, poi.name AS poi_name, "
        "       i.full_narration AS full_narration, "
        "       i.keep_exploring_audio_url AS ke_url, "
        "       i.keep_exploring_audio_duration_sec AS ke_dur, "
        "       i.keep_exploring_audio_hash AS ke_hash",
        sid=stop_id,
    ).single()
    if rec is None:
        raise HTTPException(404, f"Stop '{stop_id}' not found")

    # Phase 6 S6.6 (W6.2 R3, 11/11 "a dump, not a telling"): a MAJOR stop's tap
    # for more plays THE FULL TELLING — the writer's second composed piece with
    # its own close — never the raw keep-exploring stitch. Stops without an
    # authored full telling keep the on-demand extras route exactly as before.
    narration = rec["full_narration"] or rec["extra_narration"]
    if not narration or not narration.strip():
        raise HTTPException(409, f"Stop '{stop_id}' has no keep-exploring extras")

    # Defect 17: cap the input before TTS so a huge extra_narration can't fan out
    # into unbounded TTS chunk requests. Mirror AudioPreviewRequest's 20000-char
    # cap; truncate on a sentence boundary so the deep dive still voices cleanly.
    narration = _cap_narration(narration)

    provider_name = body.provider if body else None
    voice_id = body.voice_id if body else None
    force = body.force if body else False

    # Defect 11: cache the artifact on the ItineraryItem keyed by a hash of the
    # (capped) extra_narration. A repeat call with the same narration returns the
    # cached url WITHOUT re-running paid TTS; force=True or a changed narration
    # (hash mismatch) regenerates.
    # Defect 1: the cache key must also discriminate on provider/voice — otherwise
    # a client requesting a different voice/provider silently gets audio generated
    # with the ORIGINAL voice. Mix provider_name and voice_id into the hash so a
    # voice/provider change yields a mismatch and regenerates.
    narration_hash = _stop_narration_hash(narration, provider_name, voice_id)
    # Mobile calls this anonymously (KE5), so a cache MISS spends real TTS money
    # for an anonymous caller: bound the paid path the same way /audio/preview
    # is bounded. Checked after the cache lookup below would be too late, but
    # before it would rate-limit free cache hits — so limit only on the miss.
    if not force and rec["ke_url"] and rec["ke_hash"] == narration_hash:
        return KeepExploringAudioResponse(
            stop_id=stop_id,
            status="generated",
            audio_url=rec["ke_url"],
            duration_sec=rec["ke_dur"],
        )

    # (A provider-name resolution used to sit here purely to tell the rate
    # limiter whether this was a billed path. The limiter is gone, so it is too.
    # An unknown provider still keeps its soft-fail contract below: 200 with
    # status='failed' from generate_stop_audio, never a 400 from here.)
    try:
        gen = generate_stop_audio(
            narration,
            stop_key=f"{stop_id}-keep-exploring",
            poi_name=rec["poi_name"],
            provider_name=provider_name,
            voice_id=voice_id,
        )
    except PipelineError as e:
        return KeepExploringAudioResponse(stop_id=stop_id, status="failed", error=str(e))

    # Persist the cache on the item. This is separate from item.audio_url (the
    # scheduled per-stop tour audio) so the on-demand deep dive never masquerades
    # as the tour audio.
    session.run(
        "MATCH (i:ItineraryItem {id: $sid}) "
        "SET i.keep_exploring_audio_url = $url, "
        "    i.keep_exploring_audio_duration_sec = $dur, "
        "    i.keep_exploring_audio_hash = $hash",
        sid=stop_id,
        url=gen.audio_url,
        dur=gen.duration_sec,
        hash=narration_hash,
    )

    return KeepExploringAudioResponse(
        stop_id=stop_id,
        status="generated",
        audio_url=gen.audio_url,
        duration_sec=gen.duration_sec,
    )
