"""Automated "audio says the story" gate (live OpenAI; functional).

Generates a real Paris tour against the live dev graph (port 7687), voices a
BOUNDED sample of its stops' STITCHED per-stop narration (cold-open + transit glue
+ beats + closing) through the real OpenAI TTS provider, transcribes each MP3 back
with Whisper, and asserts the word error rate stays under the established 0.15
threshold (``tests/test_audio_functional.py``). This proves the *stitched story* —
not just an isolated beat — is faithfully voiced end-to-end.

Distinct from ``test_audio_functional.py`` (which voices isolated seed beats): this
is the TOUR-level gate over real-corpus stitched narration.

Live (OpenAI TTS + Whisper, ~cents/run) AND live-graph. Marked ``live`` and run
through ``make test-live`` (also part of ``make test``). Missing Render
credentials, provider access, or local graph data are hard failures.
"""

from __future__ import annotations

import os

import httpx
import pytest

from src.audio.eval import evaluate
from src.audio.provider import get_provider
from src.tour.beat_select import select_poi_beats
from src.tour.contract import BeatSequence, TourInput
from src.tour.generation import generate
from src.tour.render_md import stop_narration_text
from src.tour.routing_client import RoutingClient
from src.tour.selection import load_paris_corpus, select_route
from tests.live_graph import open_dev_driver

pytestmark = pytest.mark.live

# Established for TTS->Whisper round-trips on this corpus (tests/test_audio_functional.py:99).
# Reused, not re-derived: the accent-folding fix (8c0ee75) keeps French proper nouns fair.
WER_THRESHOLD = 0.15

# Bound the live spend: voice only the first N stops (~cents/run at this size).
SAMPLE_STOPS = 3

def _openai_reachable() -> bool:
    """Can we reach the OpenAI API (key set + network/proxy clear)?"""
    if not os.getenv("OPENAI_API_KEY"):
        return False
    try:
        # transport= lives on Client, not the module-level get(); bypass any
        # corporate proxy, mirroring src/audio/provider.py + eval.py.
        with httpx.Client(transport=httpx.HTTPTransport(proxy=None), timeout=5.0) as client:
            resp = client.get("https://api.openai.com/v1/models")
        return resp.status_code in (200, 401)
    except (httpx.ProxyError, httpx.ConnectError, httpx.TimeoutException):
        return False


@pytest.fixture(scope="module")
def voiced_tour():
    """Generate a real Paris tour using the Make-injected environment."""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.fail("OPENAI_API_KEY missing from fresh Render environment")
    if not _openai_reachable():
        pytest.fail("OpenAI API unreachable—check Render credential/network")

    driver = open_dev_driver()
    if driver is None:
        pytest.fail("Local dev Neo4j (7687) unreachable after live-test provisioning")
    try:
        snapshot = load_paris_corpus(driver, city_slug="paris")
    finally:
        driver.close()

    tour_input = TourInput(
        start=(48.852966, 2.349902),  # Notre-Dame / Île de la Cité (dense)
        duration_min=60,
        city_slug="paris",
        lenses=["historic_arch"],
    )
    with RoutingClient() as rc:
        route = select_route(tour_input, snapshot, routing_client=rc)
    assert route.pois, "expected a tourable route from a dense central-Paris start"

    plans = [select_poi_beats(p, snapshot.beats_for(p.id)) for p in route.pois]
    script = generate(BeatSequence(poi_beats=tuple(plans)), route, tour_input)
    per_stop = stop_narration_text(script)
    yield per_stop, route


def test_stitched_stop_audio_says_the_story(voiced_tour):
    """Each sampled stop's stitched narration, voiced + transcribed, stays under WER."""
    per_stop, route = voiced_tour
    n = min(SAMPLE_STOPS, len(per_stop))
    assert n > 0, "tour produced no per-stop narration to voice"

    provider = get_provider("openai")
    failures: list[tuple[int, float]] = []
    for idx in range(n):
        text = per_stop[idx]
        assert text and text.strip(), f"stop {idx} has empty narration"

        audio = provider.generate(text)
        assert len(audio) > 1000, f"stop {idx} audio too small: {len(audio)} bytes"

        ev = evaluate(text, audio, filename=f"stop_{idx}.mp3")
        name = route.pois[idx].name if idx < len(route.pois) else f"stop {idx}"
        print(
            f"\n  STOP {idx} ({name}): WER={ev.word_error_rate:.3f} "
            f"sim={ev.similarity_score:.3f} chars={len(text)}"
        )
        if ev.missing_words:
            print(f"    missing: {ev.missing_words[:15]}")
        if ev.extra_words:
            print(f"    extra:   {ev.extra_words[:15]}")

        if ev.word_error_rate >= WER_THRESHOLD:
            failures.append((idx, ev.word_error_rate))

    assert not failures, (
        f"{len(failures)} stop(s) exceed WER {WER_THRESHOLD}: "
        + ", ".join(f"stop {i}={w:.3f}" for i, w in failures)
    )
