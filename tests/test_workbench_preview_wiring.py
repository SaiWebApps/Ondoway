"""The workbench preview must send what it means and reject what it cannot honour.

THREE MEASURED DEFECTS (2026-07-19), all of which made "tour preview" look broken
to a human pressing the button:

1. ``city_slug`` was never sent by ``generateTourPreview`` (``frontend/review.html``),
   so ``TripPreviewRequest``'s default of "paris" applied to EVERY preview. A
   London or New York workbench session previewed the PARIS corpus from a
   London/NY start, found nothing reachable, and 422'd.

2. An unknown lens string was silently ignored. ``_lens_relation``
   (``selection.py:2451-2477``) classifies every POI as a "miss" for an
   unrecognised lens, so ``lens_relevance`` returns the uniform ``LENS_FLOOR``
   (0.25). A uniform MULTIPLICATIVE factor leaves relative ranking identical to
   passing no lens, so a typo produced the un-lensed tour with no signal at all.

3. The lens input offered its vocabulary only as a ``placeholder`` — which is not
   a value — so the default request sent NO lenses.

TWO DECISIONS (2026-07-31), both the same principle — the workbench must
replicate the tourist's app experience as closely as possible:

4. ``generateTourPreview`` opened a ``window.confirm`` spend warning before every
   preview. A tourist is never asked to approve spend, so the workbench must not
   ask either. Removed; section 4 below keeps it removed.

5. ``loadTtsProviders`` force-selected the ``mock`` TTS provider after fetching
   the provider list, silently overriding the ``openai`` default. Every workbench
   "play" was therefore a silent WAV that an editor could mistake for real
   narration — the audio twin of the standing never-mock-in-the-workbench rule.

These tests are cheap, hermetic and $0.
"""

from __future__ import annotations

import pathlib

import pytest
from pydantic import ValidationError

from src.api.models.trips import TripPreviewRequest
from src.schema.definitions import TAGGABLE_LENSES

REPO = pathlib.Path(__file__).resolve().parents[1]
REVIEW_HTML = REPO / "frontend" / "review.html"


# --------------------------------------------------------------------------
# 1. city_slug is actually sent
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 2. unknown lenses are rejected loudly
# --------------------------------------------------------------------------


def _req(**kw: object) -> TripPreviewRequest:
    base = {"center_lat": 48.8546, "center_lng": 2.3477}
    base.update(kw)
    return TripPreviewRequest(**base)  # type: ignore[arg-type]


def test_every_taggable_lens_is_accepted() -> None:
    for lens in TAGGABLE_LENSES:
        assert _req(lenses=[lens]).lenses == [lens]


def test_unknown_lens_is_rejected_not_silently_ignored() -> None:
    """UNDO TEST: remove ``validate_lenses`` and this goes RED.

    Before the validator this request was ACCEPTED and produced the un-lensed
    tour — the exact silent failure being guarded against.
    """
    with pytest.raises(ValidationError) as exc:
        _req(lenses=["dark_histry"])  # a plausible typo
    assert "Unknown lens" in str(exc.value)


def test_rejection_names_the_valid_vocabulary() -> None:
    """An error a human can act on names what they should have typed."""
    with pytest.raises(ValidationError) as exc:
        _req(lenses=["not_a_lens"])
    msg = str(exc.value)
    assert "dark_history" in msg and "historic_arch" in msg


def test_one_bad_lens_among_good_ones_still_rejects() -> None:
    with pytest.raises(ValidationError):
        _req(lenses=["dark_history", "totally_made_up"])


def test_blank_and_none_lenses_remain_permissive() -> None:
    """An absent lens is a legitimate request (the un-lensed tour), unchanged."""
    assert _req(lenses=None).lenses is None
    assert _req(lenses=[]).lenses is None
    assert _req(lenses=["  ", ""]).lenses is None
    assert _req(lenses=["  dark_history  "]).lenses == ["dark_history"]


# --------------------------------------------------------------------------
# 3. the UI offers the real vocabulary
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 4 & 5. the workbench behaves like the app
# --------------------------------------------------------------------------


def _js_function_body(html: str, declaration: str) -> str:
    """Return one JS function's source, located by plain brace matching.

    Deliberately NOT a regex: a regex that matches nothing returns a plausible
    empty string, and an "absence" assertion over an empty string passes
    vacuously — it would report the defect as fixed no matter what the file
    says. This raises instead, and every caller additionally asserts a known
    anchor is present before asserting anything is absent.
    """
    start = html.find(declaration)
    if start == -1:
        raise AssertionError(f"{declaration!r} is missing from review.html")
    open_brace = html.index("{", start)
    depth = 0
    for i in range(open_brace, len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[open_brace : i + 1]
    raise AssertionError(f"{declaration!r} is never closed in review.html")


def test_the_plan_screen_shows_what_degraded_before_anything_can_be_written() -> None:
    """A problem the operator cannot see before writing is a problem they cannot act on.

    Walking legs fall back to straight-line estimates when the routing service is
    unavailable, and that is reported as a degradation on the PLAN response. It
    has to be on screen ABOVE the "Run it like the phone" action: once that is
    clicked the day is written, voiced and paid for, so a warning that only
    appears afterwards arrives too late to change anything.

    UNDO TEST: move the ``buildDegradationPanel`` block in renderTourDay below
    the block that creates the ``tourPhoneRunBtn`` button, or delete it -> RED.
    """
    body = _js_function_body(REVIEW_HTML.read_text(), "function renderTourDay(")

    # Non-vacuity: prove we sliced the day renderer before asserting ordering.
    run_at = body.find("tourPhoneRunBtn")
    assert run_at != -1, (
        "the extracted renderTourDay body never creates the phone-run action, so "
        "this guard is not reading the plan screen"
    )
    panel_at = body.find("buildDegradationPanel(")
    assert panel_at != -1, (
        "the plan screen never builds the degradation panel, so a tour planned on "
        "estimated walking times looks exactly like a measured one"
    )
    assert panel_at < run_at, (
        "the degradation panel is built after the phone-run action, so what went "
        "wrong renders below the button that spends money on a tour"
    )


# --------------------------------------------------------------------------
# 6. the dials — every control writes into the ONE plan body,
#    every change replans automatically, and the day is honest about itself
# --------------------------------------------------------------------------


# (control id, locked wire field) — the dial vocabulary.
TOUR_DIALS = [
    ("tourDate", "start_datetime"),
    ("tourTime", "start_datetime"),
    ("tourFinish", "end_hardness"),
    ("tourParty", "party"),
    ("tourMaxLeg", "max_leg_minutes"),
    ("tourRestCadence", "rest_cadence_minutes"),
    ("tourStopDensity", "stop_density"),
    ("tourNarrationDensity", "narration_density"),
    ("tourAvoidQueues", "avoid_queues"),
    ("tourCategoryMinus", "category_minus"),
    ("tourWeather", "weather"),
]


def test_openai_is_a_real_registered_tts_provider() -> None:
    """The default the workbench picks must actually exist in the registry.

    Guards the pair: if ``openai`` were ever renamed or dropped from
    ``src/audio/provider.py``, the test above would still pass while the
    workbench silently fell through to whatever option happened to be first.
    """
    from src.audio.provider import list_providers

    providers = list_providers()
    assert "openai" in providers, (
        f"the workbench defaults to the 'openai' TTS provider, which is not "
        f"registered; available: {providers}"
    )


ONBOARD_HTML = REPO / "frontend" / "onboard.html"


def _js_function_body_after_params(html: str, declaration: str) -> str:
    """Return one JS function's body, skipping a DESTRUCTURED parameter list.

    ``_js_function_body`` above takes the first ``{`` after the declaration,
    which is right only for ``function f() {``. ``ttsPlay`` is declared
    ``async function ttsPlay({ text, cacheKey, btn, audioEl }) {``, so that first
    ``{`` is the PARAMETER object: brace matching closes on it immediately and
    yields the 32-character parameter list, which contains no request at all. A
    guard built on it passes vacuously while the defect is live — the very
    failure the sibling helper's docstring exists to prevent, arriving through a
    door that docstring did not anticipate. Using the full declaration line as
    the anchor does not help; the parameter brace is still first.

    So walk parenthesis depth from the declaration to the ``)`` that closes the
    parameter list, and only then brace-match. Still no regex.
    """
    start = html.find(declaration)
    if start == -1:
        raise AssertionError(f"{declaration!r} is missing from review.html")
    i = html.index("(", start)
    depth = 0
    while True:
        if html[i] == "(":
            depth += 1
        elif html[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
        if i >= len(html):
            raise AssertionError(f"{declaration!r} has an unclosed parameter list")
    open_brace = html.index("{", i)
    depth = 0
    for j in range(open_brace, len(html)):
        if html[j] == "{":
            depth += 1
        elif html[j] == "}":
            depth -= 1
            if depth == 0:
                return html[open_brace : j + 1]
    raise AssertionError(f"{declaration!r} is never closed in review.html")


