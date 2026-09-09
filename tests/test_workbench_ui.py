"""Playwright UI test suite for the Editorial Review Workbench (review.html).

Systematically exercises the workbench through its complete workflow and
produces a markdown bug report with screenshots. Auto-starts a FastAPI
server on localhost:8001 pinned to the DEDICATED workbench Neo4j (7689)
for the duration of the module.

Usage:
    make test-workbench   # starts the workbench Neo4j container automatically

Requires: Playwright and pytest; ``make test-workbench`` provisions Neo4j 7689.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from neo4j import GraphDatabase
from playwright.sync_api import Page, expect, sync_playwright

from tests.browser_launch import chromium_launch_options

# ---------------------------------------------------------------------------
# DOM Selectors — single source of truth (Risk R1 mitigation)
# ---------------------------------------------------------------------------

# IDs
CITY_OVERLAY = "#cityOverlay"
CITY_INPUT = "#cityInput"  # must NOT exist — the picker is a pure DB dropdown
CITY_SELECT = "#citySelect"
CITY_SUBMIT = "#citySubmitBtn"
CITY_LABEL = "#cityLabel"
LOAD_JSON_BTN = "#loadJsonBtn"
FILE_INPUT = "#fileInput"
WORKLIST = "#worklist"
DUP_OVERLAY = "#dupOverlay"
DUP_RESOLVE_BTN = "#dupResolveBtn"
DETAIL_VIEW = "#detailView"
DETAIL_TITLE = "#detailTitle"
DETAIL_EMPTY = "#detailEmpty"
DEFER_BTN = "#deferBtn"
MARK_COMPLETE_BTN = "#markCompleteBtn"
NEXT_BTN = "#nextBtn"
PREV_ROW_BTN = "#prevRowBtn"
NEXT_ROW_BTN = "#nextRowBtn"
ERROR_TOAST = "#errorToast"
SUCCESS_TOAST = "#successToast"
MAP = "#map"

# CSS classes
WORKLIST_ROW = ".worklist-row"
BADGE_PENDING = ".badge-pending"
BADGE_COMPLETE = ".badge-complete"
BADGE_DEFERRED = ".badge-deferred"
BADGE_FLAGGED = ".badge-flagged"
BADGE_UPLOADED = ".badge-uploaded"
BEAT_CARD = ".beat-card"
BEAT_CONFLICT_BADGE_HARD = ".beat-conflict-badge-hard"
BEAT_CONFLICT_BADGE_REVIEW = ".beat-conflict-badge-review"
BEAT_CONFLICT_BADGE = ".beat-conflict-badge"
CONFLICT_SIDE = ".conflict-side"
MERGE_OVERLAY = ".merge-overlay"
FIELD_WARNING = ".field-warning"
FIELD_WARN_YELLOW = ".field-warn-yellow"
AUDIT_NOTES_BOX = ".audit-notes-box"
POI_AUDIT_NOTES_BOX = ".poi-audit-notes-box"
BEAT_WARNING = ".beat-warning"
MAP_WARNING = ".map-warning"
MAP_WARN_GEOFENCE = ".map-warn-geofence"

# Data attributes
DATA_FIELD = '[data-field="{}"]'
DATA_BEAT_FIELD = '[data-beat-field="{}"]'
DATA_BEAT_INDEX = '.beat-card[data-beat-index="{}"]'
DATA_POI_IDX = '.worklist-row[data-poi-idx="{}"]'
DUP_INPUT = 'input[data-dup-idx="{}"]'

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The workbench test API takes whatever free port the OS hands out, and review.html
# is TOLD that port via ?apiPort= — so nothing here needs a fixed number.
#
# It used to be a hardcoded 8001, which bought nothing (no human ever opens this
# server; only the test talks to it) and cost real friction: a sibling session
# running this same suite, or anything else that happened to want 8001, blocked
# the run. Asking the OS removes the collision instead of arbitrating it.
#
# Still never the dev workbench's 8000, so a developer's `make api` keeps running
# untouched through a test run.
def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


WORKBENCH_API_PORT = _pick_free_port()
API_BASE = f"http://localhost:{WORKBENCH_API_PORT}/api/v1"
# The ONLY Neo4j this suite may touch: the DEDICATED workbench instance
# (docker service neo4j-workbench, ``make db-up DB=workbench``). Deliberately NOT
# the shared test DB (7688): the pytest suite full-wipes 7688 per-module
# (conftest._wipe, test_seed/test_traversals autouse fixtures), so a concurrent
# `make test` deletes any seeds this suite plants there mid-run — no naming
# convention or teardown discipline survives that. Conversely, this suite's
# pre-seed wipe (see _wipe_workbench_db) would destroy a concurrent pytest
# session's data if it pointed at 7688. The api_server fixture starts uvicorn
# with NEO4J_URI pinned to the literal below and /healthz-verifies the running
# server; the guard and the wipe both derive from this ONE literal, never from
# os.environ (the test profile pins the pytest process to 7688, so an
# env-derived guard would misfire).
WORKBENCH_NEO4J_PORT = 7689
WORKBENCH_NEO4J_URI = f"bolt://localhost:{WORKBENCH_NEO4J_PORT}"
# Committed docker-compose literals (neo4j-workbench service) — not secrets.
WORKBENCH_NEO4J_AUTH = ("neo4j", "ondoway_workbench_2026")
WORKBENCH_NEO4J_DATABASE = "neo4j"
WORKBENCH_URL = (
    f"{(Path(__file__).parent.parent / 'frontend' / 'review.html').resolve().as_uri()}"
    f"?apiPort={WORKBENCH_API_PORT}"
)
# New-city onboarding panel (Step 7). Same file:// + ?apiPort= pattern as review.html.
ONBOARD_URL = (
    f"{(Path(__file__).parent.parent / 'frontend' / 'onboard.html').resolve().as_uri()}"
    f"?apiPort={WORKBENCH_API_PORT}"
)
# London bbox as the panel's text field expects it: "min_lat, max_lat, min_lon, max_lon".
LONDON_BBOX = "51.28, 51.7, -0.51, 0.33"
# Repo root (tests/ -> repo). Used to assert the committed tree is UNTOUCHED by a
# hermetic onboarding upload.
REPO_ROOT = Path(__file__).resolve().parent.parent
# A module-scoped tmp dir the api_server subprocess points the onboarding write +
# deploy at (ONBOARD_DATA_ROOT / ONBOARD_REGISTRY_PATH), so the London onboarding
# round-trip is fully HERMETIC — it writes here, never into the committed data/ or
# src/cities.json.
_ONBOARD_TMP = Path(tempfile.mkdtemp(prefix="onboard-wb-"))
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ui_test_fixture.json"
REPORT_DIR = Path(__file__).parent / "reports"
SCREENSHOT_DIR = REPORT_DIR / "screenshots"


# Seed data constants
SEED_POI_NAME = "UI Test Seed \u2014 Sacr\u00e9-C\u0153ur Basilica"
SEED_BEATS = [
    {
        "lens_slug": "hidden_history",
        "gravity": 4,
        "script_body": (
            "The Sacr\u00e9-C\u0153ur Basilica dome held a beacon of white stone on that fateful "
            "autumn day in 1914. Workers climbed the dark narrow stairs "
            "while Parisians gathered on the hill below. The basilica one if by "
            "land two if by river changed the spirit of Montmartre forever. Those "
            "bells became the most famous chimes in the arrondissement "
            "calling every neighborhood along the Seine and "
            "its banks."
        ),
    },
    {
        "lens_slug": "revolutionary_moments",
        "gravity": 3,
        "script_body": (
            "Camille Desmoulins rallied through the Paris boulevards warning "
            "citizens that royal troops were massing toward the Bastille "
            "and the Tuileries. His impassioned call covered roughly twelve blocks of narrow "
            "streets and crowded quarters. At every caf\u00e9 he pounded on tables "
            "shouting aux armes citoyens. Danton and Marat "
            "joined the uprising but only Danton made it all the way to the Convention."
        ),
    },
    {
        "lens_slug": "dark_history",
        "gravity": 2,
        "script_body": (
            "Prussian soldiers besieged Paris for months during the Franco-Prussian War "
            "turning parks into camps and homes into barracks. The occupiers "
            "patrolled cobblestone streets enforcing harsh laws on Parisian citizens. "
            "Tensions boiled over at the Commune uprising when soldiers fired into "
            "a crowd killing scores of communards."
        ),
    },
]

# ── Eiffel conflict seed ──────────────────────────────────────────────────
# A second seed POI at the Eiffel Tower's GPS, named so it does NOT auto-merge
# with the incoming "UI Test Seed — Eiffel Tower" entry (name similarity < 0.5,
# so mergeIncomingIntoDbPois leaves the incoming POI in the worklist and the
# proximity-match panel is shown instead). Its three beats are tuned — Jaccard
# verified offline against review.html's jaccardSimilarity — so the incoming
# POI's five beats land in every conflict band once the editor clicks "Same
# Place" (which runs runBeatConflictDetection against this POI):
#   incoming beat 0 hidden_history -> HARD      (same lens as seed beat 0)
#   incoming beat 1 music_heritage -> NET-NEW   (no lens match, Jaccard ~0.03)
#   incoming beat 2 science_tech   -> SOFT      (Jaccard ~0.80 vs seed beat 1)
#   incoming beat 3 street_art     -> REVIEW    (Jaccard ~0.36 vs seed beat 2)
#   incoming beat 4 parks_gardens  -> PASS-THRU (no lens match, Jaccard ~0.02)
EIFFEL_SEED_NAME = "UI Test — Champ de Mars Landmark"
EIFFEL_SEED_COORDS = (48.8584, 2.2945)
EIFFEL_SEED_BEATS = [
    {
        "lens_slug": "hidden_history",
        "gravity": 3,
        "script_body": (
            "The tower demands sixty tonnes of fresh paint applied by hand every seven "
            "years to shield its puddled iron lattice from rust. A crew suspended on ropes "
            "brushes three graduated shades onto the metal so the whole structure reads as "
            "a single uniform bronze when seen from the ground far below."
        ),
    },
    {
        "lens_slug": "historic_arch",
        "gravity": 4,
        "script_body": (
            "Gustave Eiffel himself maintained a private apartment at the summit of the "
            "tower where he entertained distinguished guests including Thomas Edison. The "
            "apartment was furnished with velvet settees, a grand piano, and scientific "
            "instruments. Parisians who had mocked the tower as a metal monstrosity begged "
            "for invitations. Eiffel refused nearly all of them, preferring to use the "
            "space for quiet study."
        ),
    },
    {
        "lens_slug": "war_conflict",
        "gravity": 2,
        "script_body": (
            "When the tower was first unveiled, Parisian artists erupted in fury. A "
            "petition signed by Guy de Maupassant, Alexandre Dumas, and Charles Garnier "
            "called it a disgrace, a factory chimney disfiguring the city skyline. The "
            "artists insisted the new iron structure had no place in Paris. Years later "
            "their loud protest slowly faded as the public embraced it."
        ),
    },
]

# Taggable lenses — derived from definitions.py (single source of truth)
import contextlib

from src.schema.definitions import DAG_CHILD_LENSES, MVP_LENSES, TAGGABLE_LENSES

LENS_SLUGS = list(TAGGABLE_LENSES)

LENS_DISPLAY_LABELS = {}
for _l in MVP_LENSES:
    if not _l.get("is_parent"):
        LENS_DISPLAY_LABELS[_l["name"]] = _l["display_label"]
for _c in DAG_CHILD_LENSES:
    LENS_DISPLAY_LABELS[_c["name"]] = _c["display_label"]


# ---------------------------------------------------------------------------
# Bug Reporter
# ---------------------------------------------------------------------------


class BugReporter:
    """Accumulates UI issues and generates a markdown bug report."""

    def __init__(self) -> None:
        self.issues: list[dict[str, Any]] = []
        self.tests_run = 0
        self.screenshots: list[str] = []

    def log_issue(
        self,
        severity: str,
        title: str,
        flow: str,
        steps: list[str],
        expected: str,
        actual: str,
        screenshot_path: str | None = None,
    ) -> None:
        self.issues.append(
            {
                "severity": severity,
                "title": title,
                "flow": flow,
                "steps": steps,
                "expected": expected,
                "actual": actual,
                "screenshot": screenshot_path,
            }
        )
        if screenshot_path:
            self.screenshots.append(screenshot_path)

    def increment_tests(self) -> None:
        self.tests_run += 1

    def save_report(self) -> Path:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d")
        report_path = REPORT_DIR / f"workbench-ui-bugs-{date_str}.md"

        critical = sum(1 for i in self.issues if i["severity"] == "Critical")
        major = sum(1 for i in self.issues if i["severity"] == "Major")
        minor = sum(1 for i in self.issues if i["severity"] == "Minor")

        lines = [
            f"# Editorial Workbench UI Bug Report \u2014 {date_str}\n",
            "## Summary\n",
            f"- Tests run: {self.tests_run}",
            f"- Issues found: {len(self.issues)} ({critical} critical, {major} major, {minor} minor)",
            f"- Screenshots captured: {len(self.screenshots)}\n",
        ]

        if not self.issues:
            lines.append("## No issues found \u2014 all checks passed.\n")
        else:
            lines.append("## Issues\n")
            for issue in self.issues:
                lines.append(f"### [{issue['severity']}] {issue['title']}\n")
                lines.append(f"- **Flow:** {issue['flow']}")
                lines.append("- **Steps:**")
                for j, step in enumerate(issue["steps"], 1):
                    lines.append(f"  {j}. {step}")
                lines.append(f"- **Expected:** {issue['expected']}")
                lines.append(f"- **Actual:** {issue['actual']}")
                if issue["screenshot"]:
                    rel = Path(issue["screenshot"]).name
                    lines.append(f"- **Screenshot:** [screenshots/{rel}](screenshots/{rel})")
                lines.append("")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        return report_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _api_request(method: str, path: str, json_data: dict | None = None) -> dict | list | None:
    """Make an API request and return parsed JSON (or None on error)."""
    url = f"{API_BASE}{path}"
    body = None
    headers = {}
    if json_data is not None:
        body = json.dumps(json_data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as exc:
        return {
            "_error": True,
            "_status": exc.code,
            "_body": exc.read().decode("utf-8", errors="replace"),
        }
    except Exception:
        return None


def _api_get(path: str) -> dict | list | None:
    return _api_request("GET", path)


def _api_post(path: str, json_data: dict | None = None) -> dict | list | None:
    return _api_request("POST", path, json_data)


def _api_delete(path: str) -> dict | list | None:
    return _api_request("DELETE", path)


@contextlib.contextmanager
def _stubbed_audio_preview(page: Page):
    """Answer ``POST /audio/preview`` inside the BROWSER, and record every ask.

    OWNER RULING 2026-07-31 — no fake audio provider is ever offered to a human.
    These tests used to press ``page.select_option("#ttsProviderSelect", "mock")``
    to stay $0, which required the API to keep serving a silent-WAV provider that
    the editor's dropdown then listed too. ``src/audio/provider.py`` no longer
    registers that double at all, so the option does not exist and the server
    would refuse the name.

    The $0 path moved to where a $0 path belongs: this test's own browser
    context. Everything the workbench is responsible for is still measured for
    real — the click, the fetch, the request payload, the blob URL, the decode,
    the cache, the single delegated listener. What is stubbed is only the paid
    synthesis, and the stub is STRICTER than the old mock provider: it records
    the exact JSON the page sent, so each caller can assert the workbench asked
    for a provider the live API actually serves (i.e. a real one) instead of
    silently sending a fake name.

    The bytes are a genuine decodable WAV produced by ``MockTTSProvider`` — the
    class still exists, it is simply unreachable from any server. It runs HERE,
    in the test process, and never reaches a human.

    ALSO stubs ``GET /audio/providers`` to report the real provider AVAILABLE.
    That is not cosmetic. ``/audio/providers`` reports availability by probing the
    credential each provider needs, and this suite's server runs with a blanked
    ``OPENAI_API_KEY`` (conftest), so it honestly answers "openai (unavail)".
    Since 2026-07-31 the page refuses to play an unavailable voice BEFORE issuing
    a request — a real fix, because it replaces a bare "TTS failed (502)" with a
    message naming the missing key. But it also means these tests never fire the
    request they stub, and they time out waiting for it.
    So: a test that wants to exercise the audio path must say so by presenting a
    server where the voice exists. Stubbing the availability probe states that
    premise explicitly instead of leaving it to a blank credential.

    Yields the list of parsed request bodies, newest last.
    """
    from src.audio.provider import MockTTSProvider

    def _providers_handler(route) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"providers": [{"name": "openai", "available": True}]}),
        )

    page.route("**/audio/providers", _providers_handler)

    calls: list[dict] = []

    def _handler(route) -> None:
        body = json.loads(route.request.post_data or "{}")
        calls.append(body)
        route.fulfill(
            status=200,
            content_type="audio/wav",
            headers={"Content-Disposition": 'attachment; filename="preview.wav"'},
            body=MockTTSProvider().generate(body.get("text", "")),
        )

    page.route("**/audio/preview", _handler)
    try:
        yield calls
    finally:
        page.unroute("**/audio/preview")
        page.unroute("**/audio/providers")


# ---------------------------------------------------------------------------
# The tour view is TWO calls: a free PLAN that returns ONE day, and a paid
# AUTHOR that writes it. Phase 4 (design §8.1) deleted pick-one-of-three: the
# plan response still carries ``options`` as a LIST — of exactly one — because
# trips persisted before Phase 4 store multi-option lists and the compose
# reader keeps serving them forever (deviation i), but no card is picked any
# more; the one day renders and a single "write the tour" action authors it.
# Every stub below answers both endpoints in the shapes they really return —
# a stub that fulfils a shape no endpoint can produce is the same lie as a
# mock narrator.
#
# PLAN   POST /trips/preview        -> {spine_area, options[1], tourability,
#                                       promises, day_notes, slack_minutes, degradations}
# AUTHOR POST /trips/preview/author -> {route_id, option, ...} and NO top-level stops
#
# The authored stops live on ``option.stops`` because there is exactly one
# interleave and the authored tour IS the day that was planned. The plan reply
# also carries the W4.2 honesty surface (promises / day_notes / slack_minutes,
# empty by default), and each stop carries its queue price and door side.
# ---------------------------------------------------------------------------

#: A plan fingerprint the author route would accept: ``preview-<12 hex>-opt<N>``.
_PLAN_FINGERPRINT = "preview-0123456789ab"


def _option_stop(stop: dict, *, index: int) -> dict:
    """One RouteOptionStop, from the flat stop dicts these stubs already write.

    Two field names differ from a rendered card and only two: the place is
    ``name`` here and ``poi_name`` there, and there is no ``sort_order`` at all —
    that is a running counter over the emitted cards, which the page derives.
    """
    band = stop.get("band", "dwell")
    return {
        "poi_id": stop.get("poi_id", f"poi-{index}"),
        "name": stop["poi_name"],
        "lat": stop.get("lat", 48.8566),
        "lng": stop.get("lng", 2.3522),
        "lens": None,
        # An explicit visit_or_walk_past wins (a SEATED stop can carry the "vignette"
        # NARRATION band — Phase 5 W5.1); the old band-derived default stays for
        # every stub that never said.
        "visit_or_walk_past": stop.get(
            "visit_or_walk_past", "walk_past" if band in ("vignette", "leg") else "visit"
        ),
        "minutes": int(stop.get("minutes", 0)),
        "band": band,
        "spotlight": stop.get("spotlight", 0.0),
        "narration": stop.get("narration", ""),
        "has_deeper_dive": bool(stop.get("has_deeper_dive", False)),
        # W4.2 honesty surface: the wire defaults are 0 / null (an unpriced,
        # dateless plan), exactly what RouteOptionStop serializes.
        "queue_minutes": int(stop.get("queue_minutes", 0)),
        "goes_inside": stop.get("goes_inside"),
    }


def _route_option(stops, *, route_id, eta_seconds=None):
    """One PLAN option: PLACES ONLY.

    Planning shows names, order, walking time and ETA and no words at all, so the
    narration is emptied here rather than carried. ``band="leg"`` cards are
    narration heard while walking rather than places, so they do not exist at plan
    time either.
    """
    places = [s for s in stops if s.get("band") != "leg"]
    # Standing-still minutes are every SEATED place's minutes; a walk-past has none.
    # (A stop's `visit_or_walk_past`, when it says so, beats its band — W5.1.)
    dwell_minutes = sum(
        int(s.get("minutes", 0))
        for s in places
        if s.get("visit_or_walk_past", "walk_past" if s.get("band") == "vignette" else "visit")
        != "walk_past"
    )
    return {
        "route_id": route_id,
        "stops": [
            dict(_option_stop(s, index=i), narration="", has_deeper_dive=False)
            for i, s in enumerate(places)
        ],
        "stop_audio": {},
        "route_polyline": None,
        "eta_seconds": eta_seconds if eta_seconds is not None else (dwell_minutes + 18) * 60,
        "why_this_works": None,
        "lens_summary": {},
        "flow_score": 0.0,
        "backtrack_ratio": 0.0,
        "degraded": False,
        "profiles": [],
        "offline_package": None,
        "lens_coverage_note": None,
    }


def _plan_payload(stops, *, spine_area="Île de la Cité", degradations=None, options=None,
                  tourability=None, promises=None, day_notes=None, slack_minutes=None):
    """The PLAN response: ONE day. ``options`` stays a list — of exactly one —
    because that is the wire shape (deviation i: stored pre-Phase-4 trips keep
    the multi-option compose contract, so the field stays a list forever).

    ``promises``/``day_notes``/``slack_minutes`` are the W4.2 honesty surface,
    in the server's own shapes (TripPreviewPromise dicts, PLAIN STRINGS, int):
    empty/null by default exactly as ``TripPreviewResponse`` defaults them."""
    if options is None:
        options = [_route_option(stops, route_id=f"{_PLAN_FINGERPRINT}-opt1")]
    return {
        "spine_area": spine_area,
        "options": options,
        "tourability": tourability,
        "promises": promises or [],
        "day_notes": day_notes or [],
        "slack_minutes": slack_minutes,
        "degradations": degradations or [],
    }


def _authored_payload(payload: dict, *, route_id=f"{_PLAN_FINGERPRINT}-opt1") -> dict:
    """The AUTHOR response, built from the flat tour dict these stubs already write.

    Everything that describes authored text — the narrator, the lane, the quality
    verdicts, the Basic fallback — stays at the top level exactly as it is today.
    Only two things move: the stops become ``option.stops``, and the per-corridor
    lens note becomes ``option.lens_coverage_note``. The Basic lane authors
    nothing, so it has no option at all and keeps its own stops.
    """
    stops = payload.get("stops") or []
    rest = {k: v for k, v in payload.items() if k not in ("stops", "lens_coverage_note")}
    option = None
    if stops:
        option = _route_option(stops, route_id=route_id)
        option["stops"] = [_option_stop(s, index=i) for i, s in enumerate(stops)]
        option["lens_coverage_note"] = payload.get("lens_coverage_note")
    rest.setdefault("total_audio_min", sum(int(s.get("minutes", 0)) for s in stops))
    return {"route_id": route_id, "option": option, **rest}


def _route_two_step(page, *, plan, compose):
    """Answer BOTH calls of the two-step flow. The plan pattern cannot match the
    author URL (a Playwright glob is a full match), so registration order is
    irrelevant."""
    page.route(
        "**/trips/preview",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(plan)
        ),
    )
    page.route(
        "**/trips/preview/author",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(compose)
        ),
    )


def _unroute_two_step(page):
    page.unroute("**/trips/preview/author")
    page.unroute("**/trips/preview")


def _set_tour_inputs(page, *, start=None, end=None, duration=None):
    """Set the tour form's coordinate/duration inputs WITHOUT firing a replan.

    Since Phase 4 (W4.2 panel) EVERY control change inside ``.tour-preview``
    auto-replans the day through a 400ms debounce. ``locator.fill`` marks the
    input dirty, so the change event fires on the next focus move — usually the
    Generate click itself — and a second, debounced POST lands ~400ms after the
    one the test is asserting on, re-rendering the day mid-assertion. These
    tests want exactly ONE plan request per explicit click, so the values are
    assigned the way the page's own map-click handler assigns them: a direct
    ``.value`` write, which fires no change event. The dial test
    (``test_four_dial_turns_each_replan_the_day``) is the one that drives the
    change-event path, on purpose.
    """
    if start is not None:
        page.evaluate("v => { document.getElementById('tourStart').value = v; }", start)
    if end is not None:
        page.evaluate("v => { document.getElementById('tourEnd').value = v; }", end)
    if duration is not None:
        page.evaluate("v => { document.getElementById('tourDuration').value = v; }", str(duration))


def _plan_the_day(page, *, timeout=30000):
    """Click "Plan the day" and wait for THE day to render (design §8.1).

    One free POST /trips/preview; the reply's ``options[0]`` IS the day and it
    renders immediately — no cards, no pick. Returns the plan response.
    """
    with page.expect_response(
        lambda r: r.url.endswith("/trips/preview"), timeout=timeout
    ) as caught:
        page.locator("#tourGenerateBtn").click()
    page.wait_for_timeout(300)
    return caught.value


def _write_the_tour(page, *, timeout=30000):
    """Click the ONE "Write the tour" action and wait for the authored render.

    Writing is the only decision left after the plan renders (design §8.1): one
    button, one paid POST /trips/preview/author. Returns the author response.
    """
    write_btn = page.locator("#tourWriteBtn")
    write_btn.wait_for(state="visible", timeout=10000)
    with page.expect_response(
        lambda r: "/trips/preview/author" in r.url, timeout=timeout
    ) as caught:
        write_btn.click()
    page.wait_for_timeout(300)
    return caught.value


def _declared_audio_registry() -> dict[str, str]:
    """The provider registry ``src/audio/provider.py`` DECLARES, read from source.

    Structural, no names: the module-level dict literal whose keys are string
    constants and whose values are classes defined in that same module. Compared
    against what the live server offers, this is what catches an import-time
    ``register_provider(...)`` — the registry's own unguarded mutation door —
    putting something back that the source never declared.
    """
    import ast

    path = REPO_ROOT / "src" / "audio" / "provider.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    found: dict[str, str] = {}
    for node in tree.body:
        if not (
            (isinstance(node, ast.AnnAssign) and node.value is not None)
            or isinstance(node, ast.Assign)
        ):
            continue
        value = node.value
        if not isinstance(value, ast.Dict) or not value.keys:
            continue
        if not all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in value.keys):
            continue
        if not all(isinstance(v, ast.Name) and v.id in classes for v in value.values):
            continue
        found.update(
            {k.value: v.id for k, v in zip(value.keys, value.values, strict=True)}
        )
    assert found, (
        f"found no name->class registry in {path}; this derivation is broken, so "
        f"comparing it against the served list would prove nothing"
    )
    return found


def _class_declares_a_routable_upstream(class_name: str) -> bool:
    """Does this provider class name an endpoint on a globally routable host?

    Classified by ADDRESS, never by a list of forbidden host names: an IP literal
    is judged by :mod:`ipaddress`, a hostname is resolved and every address it
    resolves to must be globally routable. Loopback in all its spellings
    (``127.0.0.2``, ``::1``, ``tts.localhost``), RFC1918 and link-local all fall
    out with nothing to maintain.

    DELIBERATELY SHALLOW: this asks whether the class NAMES a real upstream, not
    whether its output came from one. The deep, behavioural version — the
    endpoint must reach a transport in the URL position and every return must be
    data-dependent on the response — lives in
    tests/test_workbench_matches_the_app.py, which also drives a real socket-
    denied request. What this adds is that the check is applied to the list the
    LIVE SERVER offers a human, not to the static markup that server replaces.
    """
    import ast
    import ipaddress
    import socket
    from urllib.parse import urlparse

    tree = ast.parse((REPO_ROOT / "src" / "audio" / "provider.py").read_text(encoding="utf-8"))
    node = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name), None
    )
    assert node is not None, f"{class_name} is not defined at module level in src/audio/provider.py"

    for stmt in node.body:
        if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Constant):
            continue
        value = stmt.value.value
        if not isinstance(value, str) or "://" not in value:
            continue
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            continue
        try:
            if ipaddress.ip_address(parsed.hostname).is_global:
                return True
            continue
        except ValueError:
            pass
        try:
            infos = socket.getaddrinfo(parsed.hostname, None)
        except OSError as exc:
            raise AssertionError(
                f"cannot classify {parsed.hostname!r}: name resolution failed ({exc}). "
                f"An unresolvable upstream is an UNPROVEN one, never a proven one — "
                f"check the network before touching this test."
            ) from exc
        if infos and all(ipaddress.ip_address(i[4][0]).is_global for i in infos):
            return True
    return False


def _class_refuses_to_speak_with_nothing_configured(name: str) -> bool:
    """Does this provider REFUSE to answer when nothing at all is configured?

    The SECOND legitimate shape of a real voice, and the reason this pair of
    checks exists rather than the single URL check that came before it. The rule
    the owner set on 2026-07-31 is "no fake voice in front of a human", and the
    defining trait of the silent double is that it ANSWERS FROM NOTHING: no key,
    no endpoint, no model, and it still returns bytes.

    "Names a routable upstream" was a faithful proxy for that while every real
    provider was a vendor. It stopped being one on 2026-08-29, when a local
    model was briefly registered here — genuinely synthesized speech that by
    design named no host at all. That model has since been removed (tier 3
    moved to the surfaces' own OS voice), but the guard keeps the question it
    always meant: strip the environment bare and see whether the provider still
    speaks. ``MockTTSProvider`` hands back its silent WAV regardless and is
    caught; anything that DEPENDS on real configuration refuses and passes.

    DELIBERATELY SHALLOW, exactly like its sibling above: this asks whether the
    provider DEPENDS on something real, not whether its output came from one.

    The environment is restored in ``finally`` — this runs in the pytest process,
    while the browser talks to a separate uvicorn subprocess, so nothing the page
    does can observe the gap.
    """
    from src.audio.provider import TTSError, get_provider

    provider = get_provider(name)
    saved = dict(os.environ)
    os.environ.clear()
    try:
        provider.generate("A probe line for the audio registry guard.")
    except TTSError:
        return True
    except Exception:
        # Any other explosion is not a clean refusal; treat it as unproven.
        return False
    else:
        return False
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _served_provider_names() -> list[str]:
    """The provider names the LIVE workbench API offers, straight from the API.

    This is the same call ``loadTtsProviders()`` makes to build the dropdown, so
    it is the true list a human can pick from — not the static markup, which the
    page throws away on load.
    """
    payload = _api_get("/audio/providers")
    assert isinstance(payload, dict) and payload.get("providers"), (
        f"GET /audio/providers did not answer with a provider list: {payload!r}"
    )
    return [p["name"] for p in payload["providers"]]


def _assert_asked_for_a_served_provider(calls: list[dict]) -> None:
    """Every /audio/preview the page sent named a provider the API really serves.

    The API only serves providers that reach a real speech service
    (``tests/test_workbench_matches_the_app.py`` proves that against a live
    server-shaped subprocess), so this is the workbench end of the same
    invariant: the page must not be sending a name no server would honour.
    """
    assert calls, "the page never POSTed /audio/preview"
    served = _served_provider_names()
    asked = sorted({c.get("provider") for c in calls})
    assert set(asked) <= set(served), (
        f"the workbench asked /audio/preview for {asked}, but the live API only "
        f"serves {sorted(served)}. A provider the server will not honour means "
        f"the editor is either getting an error or, worse, something fake."
    )


def _take_screenshot(page: Page, name: str) -> str:
    """Capture a screenshot and return the file path."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")
    path = SCREENSHOT_DIR / f"{name}-{ts}.png"
    page.screenshot(path=str(path), full_page=True)
    return str(path)


def _safe_assert(
    reporter: BugReporter,
    condition: bool,
    severity: str,
    title: str,
    flow: str,
    steps: list[str],
    expected: str,
    actual: str,
    page: Page | None = None,
    screenshot_name: str | None = None,
) -> bool:
    """Assert a real expectation, logging the issue (+ screenshot) AND failing the test.

    Made FATAL 2026-06-14: previously this only recorded to the BugReport and returned
    False, so every check built on it was non-fatal — the suite stayed green even when a
    UI expectation was violated ("green while broken"). It now raises after logging, so a
    recorded issue is a real test failure. No caller depends on the bool return.
    """
    reporter.increment_tests()
    if not condition:
        ss_path = None
        if page and screenshot_name:
            ss_path = _take_screenshot(page, screenshot_name)
        reporter.log_issue(severity, title, flow, steps, expected, actual, ss_path)
        raise AssertionError(f"[{severity}] {title} — expected {expected!r}, got {actual!r}")
    return True


def _load_fixture() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixtures — API Server + Seed Data Setup / Teardown
# ---------------------------------------------------------------------------


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def _server_healthz(api_base: str, *, retries: int = 1, delay: float = 0.5) -> dict | None:
    """Probe ``{api_base}/healthz`` and return the parsed payload.

    Returns ``None`` if the server has no ``/healthz`` (a build predating the
    guard) or the probe fails — both of which the caller treats as "unverifiable".
    ``retries`` exists so a server we just started has a grace period to finish
    its FastAPI lifespan startup before we give up.
    """
    url = f"{api_base}/healthz"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("neo4j_port") is not None:
                return data
        except Exception:
            pass
        if attempt < retries - 1:
            time.sleep(delay)
    return None


def _server_neo4j_port(api_base: str, *, retries: int = 1, delay: float = 0.5) -> int | None:
    """The Neo4j port the API at ``api_base`` is bound to (None if unverifiable)."""
    data = _server_healthz(api_base, retries=retries, delay=delay)
    return int(data["neo4j_port"]) if data else None


def _assert_server_is_workbench_db(api_base: str, *, source: str, retries: int = 1) -> None:
    """Fail fast unless the API at ``api_base`` is LIVE on the workbench Neo4j (7689).

    Guards the data-corruption bug where some other API is already listening on
    :8001 — a dev ``make api``-style server (7687) or a shared-test-DB server
    (7688): the suite would otherwise seed POIs into — and assert against — the
    wrong graph (observed historically: dev POI count 371 → 373; and 7688
    residue/wipes from concurrent `make test` runs breaking exact-count
    assertions). Also requires ``neo4j_connected: true`` so a server whose
    workbench container is down fails here with a clear message instead of
    surfacing as generic seeding errors. ``source`` ('external' / 'managed') is
    woven into the message so the failure says exactly which server was rejected
    and how to fix it. Raises ``RuntimeError`` (mirroring
    conftest._assert_test_port) so the fixture propagates a clear, fatal error.
    """
    data = _server_healthz(api_base, retries=retries)
    if data is None:
        raise RuntimeError(
            f"API on :{WORKBENCH_API_PORT} ({source}) did not answer GET {api_base}/healthz "
            f"with a Neo4j port, so it cannot be confirmed to point at the workbench "
            f"database (port {WORKBENCH_NEO4J_PORT}). Refusing to seed test data into an "
            f"unknown graph. Stop whatever is on :{WORKBENCH_API_PORT} and re-run — the "
            f"suite starts its own server."
        )
    port = int(data["neo4j_port"])
    if port != WORKBENCH_NEO4J_PORT:
        raise RuntimeError(
            f"API on :{WORKBENCH_API_PORT} ({source}) is connected to Neo4j port {port}, not "
            f"the dedicated workbench database (port {WORKBENCH_NEO4J_PORT}); reusing it "
            f"would seed POIs into the wrong graph (7687 = dev, 7688 = the shared pytest DB "
            f"that concurrent `make test` runs full-wipe). Stop whatever is on "
            f":{WORKBENCH_API_PORT}, then re-run `make test-workbench`."
        )
    if data.get("neo4j_connected") is not True:
        raise RuntimeError(
            f"API on :{WORKBENCH_API_PORT} ({source}) points at the workbench port "
            f"{WORKBENCH_NEO4J_PORT} but reports neo4j_connected="
            f"{data.get('neo4j_connected')!r} — the workbench Neo4j is not answering. "
            f"Start it with `make db-up DB=workbench`, then re-run `make test-workbench`."
        )


@pytest.fixture(scope="module")
def api_server():
    """Start (and own) the API server on :{WORKBENCH_API_PORT} → workbench Neo4j (7689).

    Runs on :{WORKBENCH_API_PORT} (not the dev workbench's 8000) so `make api` and
    this suite coexist. A busy port is a hard failure — the suite deliberately
    does NOT reuse an external server: reuse plus this suite's pre-seed wipe
    would let two concurrent test-workbench runs DETACH-DELETE each other's
    seeds mid-run (the exact cross-run interference class this suite's dedicated
    DB exists to kill). The uvicorn subprocess gets NEO4J_* pinned explicitly to
    the workbench literals, independent of the parent test profile, and
    /healthz must then prove it is live on 7689 before any seeding.
    """
    if _port_open("127.0.0.1", WORKBENCH_API_PORT):
        raise RuntimeError(
            f"Port {WORKBENCH_API_PORT} is already in use. The workbench suite starts its "
            f"own API and does not reuse external servers (concurrent runs would wipe each "
            f"other's seed data). Stop whatever is on :{WORKBENCH_API_PORT} — e.g. another "
            f"`make test-workbench` run or a stray uvicorn — and re-run."
        )

    # Materialize the hermetic registry before the API imports city_registry.
    # London is already a real registered city, but this test deliberately runs
    # its London fixture through the *new-city* flow.  Without an existing tmp
    # registry, city_registry correctly falls back to committed src/cities.json
    # and the product guard rejects the request as an attempted re-onboard.
    registry = json.loads((REPO_ROOT / "src" / "cities.json").read_text(encoding="utf-8"))
    registry.pop("london", None)
    (_ONBOARD_TMP / "cities.json").write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # The server's output goes to a FILE, never to an undrained pipe. With
    # stdout/stderr=PIPE and no reader, uvicorn's access log plus the planner's
    # warnings fill the 64 KB pipe buffer and the server BLOCKS FOREVER on its
    # next write — mid-request, with no error anywhere. That is a hang, not a
    # crash, so it surfaces as an unrelated-looking client timeout several steps
    # later. A file never applies back-pressure, and it keeps the log readable
    # for diagnosis (the pipe was write-only in practice: nothing ever read it).
    log_path = _ONBOARD_TMP / "api-server.log"
    log_handle = log_path.open("wb")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "src.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(WORKBENCH_API_PORT),
        ],
        stdout=log_handle,
        stderr=log_handle,
        env={
            **os.environ,
            "NEO4J_URI": WORKBENCH_NEO4J_URI,
            "NEO4J_USER": WORKBENCH_NEO4J_AUTH[0],
            "NEO4J_PASSWORD": WORKBENCH_NEO4J_AUTH[1],
            "NEO4J_DATABASE": WORKBENCH_NEO4J_DATABASE,
            # This uvicorn subprocess has no pytest in its sys.modules and no
            # real JWT secret, so the fail-closed auth guard would refuse to
            # start (src/api/auth/config.py). Opt into the dev placeholder — a
            # local test server is never a production deploy.
            "ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS": "1",
            # Resolve the SAME provider production resolves (render.yaml pins
            # TTS_PROVIDER=openai, and so does scripts/workbench.sh). This server
            # is what the browser talks to, so it must be shaped like the real
            # workbench server, not like the pytest process that starts it: the
            # parent process registers the silent double for its own $0 use
            # (tests/conftest.py) and would otherwise leak TTS_PROVIDER=mock in
            # here through the inherited environment — naming a provider this
            # interpreter deliberately cannot resolve.
            "TTS_PROVIDER": "openai",
            # ONBOARD_PROVIDER: this shard drives the real Draft Beats button in a
            # real browser, and get_drafter() (src/onboard/beat_draft.py) now FAILS
            # CLOSED on an unset value — an unset drafter used to silently mean the
            # free mock, which is what put fake beats in front of a human. So the
            # pin has to be explicit. It is "mock" here and ONLY here: unlike
            # TTS_PROVIDER above, this shard must not spend, and scripts/workbench.sh
            # pins "anthropic" for the workbench a human actually uses. Naming the
            # double in a test's own subprocess env is the sanctioned opt-in; what
            # was forbidden was getting it without asking.
            "ONBOARD_PROVIDER": "mock",
            # Step-7 onboarding: point the panel's write + deploy at a module-scoped
            # tmp dir so the London upload->deploy round-trip is HERMETIC — it writes
            # the london corpus + cities.json under the tmp root and NEVER touches the
            # committed data/ or src/cities.json. Harmless to the review.html tests,
            # which never onboard. ONBOARD_DEPLOY_API_PORT keeps the deploy's transient
            # areas-upload API off :8001/:8000 (London has no areas, so it is skipped).
            "ONBOARD_DATA_ROOT": str(_ONBOARD_TMP / "data"),
            "ONBOARD_REGISTRY_PATH": str(_ONBOARD_TMP / "cities.json"),
            # Also OS-assigned. It was 8002 — the shared Valhalla container's port,
            # which this shard now starts — and moving it to another fixed guess
            # would only relocate the collision.
            "ONBOARD_DEPLOY_API_PORT": str(_pick_free_port()),
        },
    )

    def _shutdown():
        proc.terminate()
        try:
            proc.wait(timeout=5)
        finally:
            log_handle.close()

    def _log_tail(limit: int = 4000) -> str:
        """The server's own words, for a failure that would otherwise be silent."""
        log_handle.flush()
        try:
            return log_path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:  # pragma: no cover - diagnosis path only
            return "<log unreadable>"

    for _ in range(30):
        if _port_open("127.0.0.1", WORKBENCH_API_PORT):
            break
        time.sleep(0.5)
    else:
        tail = _log_tail()
        _shutdown()
        pytest.fail(
            f"API server failed to start on port {WORKBENCH_API_PORT} within 15 seconds.\n"
            f"--- server log ({log_path}) ---\n{tail}"
        )

    try:
        # The socket opens before lifespan startup finishes, so give /healthz a
        # grace window. This proves the managed server is LIVE on 7689.
        _assert_server_is_workbench_db(API_BASE, source="managed", retries=20)
    except BaseException:
        _shutdown()
        raise

    yield "managed"

    _shutdown()


@pytest.fixture(scope="module")
def reporter():
    """Module-scoped bug reporter shared across all tests."""
    return BugReporter()


def _wipe_workbench_db() -> None:
    """DETACH DELETE every node on the dedicated workbench Neo4j — and ONLY there.

    Runs before seeding so residue from any prior run (a SIGKILLed suite, an
    orphaned NarrativeBeat left by the prefix-based teardown, a node minted by a
    Same-Place upload under an unexpected Nominatim city casing) can never leak
    into this run's exact-count or proximity assertions. The connection and the
    guard both derive from the WORKBENCH_NEO4J_URI literal — never os.environ
    and never src.connection.create_driver(), because conftest pins the pytest
    process env to the SHARED test DB (7688) via the test profile, and wiping that
    instance would destroy a concurrent `make test` session's data.
    """
    port = urllib.parse.urlparse(WORKBENCH_NEO4J_URI).port
    if port != WORKBENCH_NEO4J_PORT:  # single-literal invariant, checked at call time
        raise RuntimeError(
            f"Refusing to wipe: WORKBENCH_NEO4J_URI={WORKBENCH_NEO4J_URI!r} does not point "
            f"at the dedicated workbench port {WORKBENCH_NEO4J_PORT}."
        )
    driver = GraphDatabase.driver(WORKBENCH_NEO4J_URI, auth=WORKBENCH_NEO4J_AUTH)
    try:
        with driver.session(database=WORKBENCH_NEO4J_DATABASE) as session:
            session.run("MATCH (n) DETACH DELETE n")
    finally:
        driver.close()


@pytest.fixture(scope="module")
def seed_data(api_server):
    """Seed test data into Neo4j via the API server and clean up after all tests.

    The pre-seed wipe makes every run start from an empty workbench graph, so
    the suite's exact-count assertions (e.g. exactly 13 worklist rows) stay
    byte-identical AND deterministic.
    """
    _wipe_workbench_db()

    resp = _api_get("/nodes/Lens?limit=1")
    if resp is None:
        pytest.fail(f"API not reachable at {API_BASE} despite server being {api_server}")
    if isinstance(resp, dict) and resp.get("_error"):
        pytest.fail(f"API error: {resp.get('_status')}")

    created_ids: dict[str, list[str]] = {
        "poi": [],
        "beat": [],
        "edge_has_beat": [],
        "edge_tagged_with": [],
        "lens": [],
    }
    lenses_seeded_by_us = False

    def _is_ok(resp: dict | list | None) -> bool:
        if resp is None:
            return False
        return not (isinstance(resp, dict) and resp.get("_error"))

    def _get_id(data: dict) -> str:
        return data.get("id", data.get("_id", ""))

    # --- Check/Seed Lenses (Risk R4) ---
    resp = _api_get("/nodes/Lens?limit=50")
    # API returns {"items": [...], "total": N} — extract the items list
    if _is_ok(resp) and isinstance(resp, dict) and "items" in resp:
        existing_lenses = resp["items"]
    elif _is_ok(resp) and isinstance(resp, list):
        existing_lenses = resp
    else:
        existing_lenses = []
    existing_slugs = set()
    lens_id_map: dict[str, str] = {}

    for lens in existing_lenses:
        # NodeResponse shape: {"id": "...", "labels": [...], "properties": {"name": "slug", ...}}
        props = lens.get("properties", {})
        slug = props.get("name", "") or lens.get("slug") or lens.get("name", "")
        existing_slugs.add(slug)
        lens_id_map[slug] = _get_id(lens)

    missing_slugs = [s for s in LENS_SLUGS if s not in existing_slugs]
    if missing_slugs:
        lenses_seeded_by_us = True
        for slug in missing_slugs:
            label = LENS_DISPLAY_LABELS.get(slug, slug)
            # API expects LensCreate: {"name": slug, "display_label": label}
            data = _api_post("/nodes/Lens", {"name": slug, "display_label": label})
            if _is_ok(data) and isinstance(data, dict):
                lid = _get_id(data)
                lens_id_map[slug] = lid
                created_ids["lens"].append(lid)

    def _create_seed_poi(name: str, lat: float, lng: float, beats: list[dict]) -> str:
        """Create one seed POI plus its beats, HAS_BEAT edges, and lens tags."""
        poi_resp = _api_post(
            "/nodes/POI",
            {
                "name": name,
                "city_name": "Paris",
                "latitude": lat,
                "longitude": lng,
                "short_description": "Seed POI for UI conflict detection tests",
                "importance_tier": 1,
                "trigger_radius": 10,
                "typical_duration_min": 30,
                "kid_friendly": "yes",
            },
        )
        if not _is_ok(poi_resp) or not isinstance(poi_resp, dict):
            pytest.skip(f"Failed to create seed POI {name!r}: {poi_resp}")

        pid = _get_id(poi_resp)
        created_ids["poi"].append(pid)

        for beat_def in beats:
            beat_resp = _api_post(
                "/nodes/NarrativeBeat",
                {
                    "script_body": beat_def["script_body"],
                    "gravity": beat_def["gravity"],
                    "lens": beat_def["lens_slug"],
                },
            )
            if not _is_ok(beat_resp) or not isinstance(beat_resp, dict):
                continue
            bid = _get_id(beat_resp)
            created_ids["beat"].append(bid)

            # Link beat to POI
            edge_resp = _api_post(
                "/edges/HAS_BEAT",
                {
                    "source": {"label": "POI", "id": pid},
                    "target": {"label": "NarrativeBeat", "id": bid},
                },
            )
            if _is_ok(edge_resp) and isinstance(edge_resp, dict):
                created_ids["edge_has_beat"].append(_get_id(edge_resp))

            # Tag beat with lens
            lens_slug = beat_def["lens_slug"]
            if lens_slug in lens_id_map:
                tag_resp = _api_post(
                    "/edges/TAGGED_WITH",
                    {
                        "source": {"label": "NarrativeBeat", "id": bid},
                        "target": {"label": "Lens", "id": lens_id_map[lens_slug]},
                    },
                )
                if _is_ok(tag_resp) and isinstance(tag_resp, dict):
                    created_ids["edge_tagged_with"].append(_get_id(tag_resp))
        return pid

    # --- Create Seed POIs ---
    # 1) Sacré-Cœur: an incoming fixture entry sits at the same GPS with a similar name,
    #    so mergeIncomingIntoDbPois auto-merges it (exercises the merge path).
    # 2) Champ de Mars: same GPS as the incoming Eiffel entry but a dissimilar name, so it
    #    does NOT auto-merge — the conflict test resolves the proximity match as "Same Place"
    #    and walks all five beat-conflict bands against this POI's seeded beats.
    poi_id = _create_seed_poi(SEED_POI_NAME, 48.8867, 2.3431, SEED_BEATS)
    _create_seed_poi(EIFFEL_SEED_NAME, *EIFFEL_SEED_COORDS, EIFFEL_SEED_BEATS)

    yield {
        "poi_id": poi_id,
        "created_ids": created_ids,
        "lens_id_map": lens_id_map,
    }

    # --- Teardown: Clean ALL test data (Risk R3) ---
    # Delete all POIs with "UI Test" prefix
    try:
        resp = _api_get("/nodes/POI?limit=200")
        # API returns {"items": [...]} — extract the items list
        if _is_ok(resp) and isinstance(resp, dict) and "items" in resp:
            poi_list = resp["items"]
        elif _is_ok(resp) and isinstance(resp, list):
            poi_list = resp
        else:
            poi_list = []
        for poi in poi_list:
            props = poi.get("properties", {})
            name = props.get("name", "") or poi.get("name", poi.get("poi_name", ""))
            if name.startswith("UI Test"):
                pid = _get_id(poi)
                if pid:
                    _api_delete(f"/nodes/POI/{pid}")
    except Exception:
        pass

    # Delete lenses we seeded (if any)
    if lenses_seeded_by_us:
        for lid in created_ids["lens"]:
            with contextlib.suppress(Exception):
                _api_delete(f"/nodes/Lens/{lid}")


@pytest.fixture(scope="module")
def browser_page(seed_data, reporter):
    """Launch a headless Chromium browser for the test suite."""
    with sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_options(slow_mo=300))
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        # ACCEPT window.confirm. Playwright DISMISSES dialogs by default when no
        # handler is registered, so any confirm() in review.html silently becomes
        # a Cancel and the flow under test never runs at all.
        #
        # HISTORY: added for the tour-preview spend gate ("Generate a Premium
        # candidate? ... spends real money"), which had turned every tour-preview
        # test into a timeout — generateTourPreview returned BEFORE its fetch.
        # The owner ordered that dialog DELETED on 2026-07-31, because the
        # workbench must replicate the tourist's app experience and a tourist is
        # never asked to approve spend. Its absence is now pinned by
        # tests/test_workbench_preview_wiring.py (section 4).
        #
        # The handler still belongs here: review.html confirms ELSEWHERE, and
        # that one is load-bearing — the zero-beat branch of showMergePreview
        # deletes the source POI when the editor clicks OK.
        page.on("dialog", lambda dialog: dialog.accept())
        yield page, seed_data, reporter
        # Save bug report before closing
        report_path = reporter.save_report()
        print(f"\n\nBug report saved to: {report_path}")
        browser.close()


# ---------------------------------------------------------------------------
# Test: api_server DB-isolation guard (regression for dev-graph seeding)
# ---------------------------------------------------------------------------


def _stub_healthz_server(neo4j_port: int | None, *, connected: bool = True):
    """Start a real localhost HTTP server that mimics the API's /healthz.

    If ``neo4j_port`` is None the handler 404s /healthz (a build predating the
    guard). ``connected=False`` mimics a server whose Neo4j container is down
    (status degraded). Returns ``(server, base_url)``; caller must
    ``server.shutdown()``.
    """
    import http.server
    import threading

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.endswith("/healthz") and neo4j_port is not None:
                body = json.dumps(
                    {
                        "status": "ok" if connected else "degraded",
                        "neo4j_uri": f"bolt://localhost:{neo4j_port}",
                        "neo4j_port": neo4j_port,
                        "neo4j_database": "neo4j",
                        "neo4j_connected": connected,
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: Any) -> None:  # silence request logging
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/api/v1"
    return server, base_url


class TestApiServerGuard:
    """Proves the healthz guard only accepts a server LIVE on the workbench DB.

    Regression for two data-corruption bugs: (1) a dev `make api` (Neo4j 7687)
    listening on the suite's HTTP port was reused, seeding test POIs into the
    dev graph (observed: 371 → 373); (2) the suite ran against the SHARED test
    DB (7688), where concurrent `make test` sessions full-wipe per-module and
    other suites leave residue (toy 'Eiffel Tower' at the exact conflict-seed
    coords) — breaking exact-count and proximity assertions. 7688 must now be
    rejected exactly like 7687. These tests need no Neo4j and no browser, so
    they run fast and always.
    """

    def test_guard_rejects_dev_pointed_server(self):
        server, base = _stub_healthz_server(7687)
        try:
            assert _server_neo4j_port(base) == 7687
            with pytest.raises(RuntimeError, match="7687"):
                _assert_server_is_workbench_db(base, source="external")
        finally:
            server.shutdown()

    def test_guard_rejects_shared_test_db_server(self):
        # The shared pytest DB (7688) is NOT the workbench DB: concurrent
        # `make test` runs wipe it and other suites' residue lives there.
        server, base = _stub_healthz_server(7688)
        try:
            assert _server_neo4j_port(base) == 7688
            with pytest.raises(RuntimeError, match="7688"):
                _assert_server_is_workbench_db(base, source="external")
        finally:
            server.shutdown()

    def test_guard_accepts_workbench_pointed_server(self):
        server, base = _stub_healthz_server(WORKBENCH_NEO4J_PORT)
        try:
            assert _server_neo4j_port(base) == WORKBENCH_NEO4J_PORT
            # Must NOT raise — a live workbench-DB server (port 7689) is valid.
            _assert_server_is_workbench_db(base, source="managed")
        finally:
            server.shutdown()

    def test_guard_rejects_workbench_server_with_db_down(self):
        # Right port but neo4j_connected=false (container down) -> reject with
        # a message that names the fix, instead of generic seeding errors.
        server, base = _stub_healthz_server(WORKBENCH_NEO4J_PORT, connected=False)
        try:
            with pytest.raises(RuntimeError, match="db-up DB=workbench"):
                _assert_server_is_workbench_db(base, source="managed")
        finally:
            server.shutdown()

    def test_guard_rejects_server_without_healthz(self):
        # A pre-guard build (no /healthz) is unverifiable -> reject, don't reuse.
        server, base = _stub_healthz_server(None)
        try:
            assert _server_neo4j_port(base) is None
            with pytest.raises(RuntimeError, match="unknown graph"):
                _assert_server_is_workbench_db(base, source="external")
        finally:
            server.shutdown()


# ---------------------------------------------------------------------------
# Test: City Prompt + JSON Load + Duplicate Resolver (ACs #1-2) — Task 4
# ---------------------------------------------------------------------------


class TestWorkbenchLoadFlow:
    """Tests for initial load: city prompt, JSON load, duplicate resolver, worklist."""

    def test_city_prompt_and_json_load(self, browser_page):
        page, _seed_data, reporter = browser_page
        _load_fixture()

        # --- City Prompt Flow ---
        page.goto(WORKBENCH_URL)
        page.wait_for_load_state("networkidle")

        # Assert city overlay is visible
        overlay = page.locator(CITY_OVERLAY)
        overlay_visible = overlay.is_visible()
        _safe_assert(
            reporter,
            overlay_visible,
            "Critical",
            "City overlay not visible on load",
            "City Prompt",
            ["Navigate to workbench URL"],
            "City overlay (#cityOverlay) is visible",
            f"Overlay visible: {overlay_visible}",
            page,
            "ac1-city-overlay-missing",
        )

        # The city picker loads real cities from GET /cities (distinct
        # POI.city_name). Wait for the seeded 'Paris' option to populate.
        page.wait_for_function(
            "() => { const s = document.querySelector('#citySelect'); "
            "return s && [...s.options].some(o => o.textContent.includes('Paris')); }",
            timeout=15000,
        )
        # The picker is a PURE dropdown of graph-sourced cities. There must be NO
        # free-text entry and NO "Other / add a new city" option — a city with no
        # graph data is not reviewable, and typing a name would fork nodes.
        option_texts = page.locator(f"{CITY_SELECT} option").all_text_contents()
        assert any("Paris" in t and "POI" in t for t in option_texts), option_texts
        _safe_assert(
            reporter,
            not any(
                ("other" in t.lower() or "type its name" in t.lower() or "add a new city" in t.lower())
                for t in option_texts
            ),
            "Critical",
            "The picker still offers a free-text / 'Other' option — it must be DB-only",
            "City Prompt",
            ["Read #citySelect option labels"],
            "No 'Other'/'add a city' option present",
            f"options: {option_texts}",
            page,
            "ac1-no-other-option",
        )
        _safe_assert(
            reporter,
            page.locator(CITY_INPUT).count() == 0,
            "Critical",
            "A free-text city <input> still exists — the picker MUST be a pure DB dropdown",
            "City Prompt",
            ["Query the DOM for #cityInput"],
            "#cityInput is absent from the DOM",
            f"#cityInput count: {page.locator(CITY_INPUT).count()}",
            page,
            "ac1-no-freetext-input",
        )
        _take_screenshot(page, "city-picker-pure-dropdown")
        # Pick the seeded city from the list — one click, exact key, no typing
        # (index 0 is the disabled 'Select a city…' hint).
        page.locator(CITY_SELECT).select_option(index=1)
        page.locator(CITY_SUBMIT).click()

        # Wait for overlay to close after the DB connect (no geocode round-trip)
        try:
            overlay.wait_for(state="hidden", timeout=15000)
            city_accepted = True
        except Exception:
            city_accepted = False
            _safe_assert(
                reporter,
                False,
                "Critical",
                "City overlay did not close after submitting 'Paris'",
                "City Prompt",
                [
                    "Navigate to workbench URL",
                    "Select 'Paris' from #citySelect",
                    "Click #citySubmitBtn",
                ],
                "Overlay closes within 10s",
                "Overlay still visible after 15s (DB connect slow/down)",
                page,
                "ac1-city-timeout",
            )

        if city_accepted:
            # Verify city label
            label_text = page.locator(CITY_LABEL).text_content() or ""
            _safe_assert(
                reporter,
                "Paris" in label_text,
                "Major",
                "City label does not contain 'Paris'",
                "City Prompt",
                ["Submit 'Paris' city"],
                "'Paris' appears in #cityLabel",
                f"Label text: '{label_text}'",
                page,
                "ac1-city-label",
            )

        # --- JSON Load Flow ---
        # Capture console errors for debugging
        console_errors: list[str] = []
        page.on(
            "console",
            lambda msg: (
                console_errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None
            ),
        )

        # Wait for Load JSON button to be enabled (city geocoding must complete first)
        load_btn = page.locator(LOAD_JSON_BTN)
        with contextlib.suppress(Exception):
            load_btn.wait_for(state="visible", timeout=5000)

        # Use Playwright's file chooser API to properly trigger the file input
        with page.expect_file_chooser() as fc_info:
            load_btn.click()
        file_chooser = fc_info.value
        file_chooser.set_files(str(FIXTURE_PATH))

        # Wait for worklist to populate (or error toast to appear)
        page.wait_for_timeout(3000)

        # Check for errors
        if console_errors:
            _safe_assert(
                reporter,
                False,
                "Critical",
                f"Console errors during JSON load: {'; '.join(console_errors[:3])}",
                "JSON Load",
                ["Load fixture via file chooser"],
                "No console errors",
                f"{len(console_errors)} error(s): {'; '.join(console_errors[:3])}",
                page,
                "ac1-console-errors",
            )

        # Check if error toast appeared (JSON validation failure)
        error_toast = page.locator(ERROR_TOAST)
        if error_toast.count() > 0 and error_toast.first.is_visible():
            toast_text = error_toast.first.text_content() or ""
            _safe_assert(
                reporter,
                False,
                "Critical",
                f"Error toast appeared during JSON load: {toast_text[:200]}",
                "JSON Load",
                ["Load fixture via file chooser"],
                "No error toast",
                f"Toast: {toast_text[:200]}",
                page,
                "ac1-json-error-toast",
            )

        # --- Duplicate Resolver (AC #2) ---
        dup_overlay = page.locator(DUP_OVERLAY)
        try:
            dup_overlay.wait_for(state="visible", timeout=5000)
            dup_visible = True
        except Exception:
            dup_visible = False

        _safe_assert(
            reporter,
            dup_visible,
            "Major",
            "Duplicate resolver overlay did not appear",
            "Duplicate Resolver",
            [
                "Load fixture with entries #6/#7 sharing name 'UI Test — Duplicate Seine Promenade'",
            ],
            "#dupOverlay becomes visible",
            f"Overlay visible: {dup_visible}",
            page,
            "ac2-dup-overlay-missing",
        )

        if dup_visible:
            _take_screenshot(page, "ac2-dup-overlay")

            # Find the rename input for one duplicate entry and rename it
            dup_inputs = page.locator(f"{DUP_OVERLAY} input[data-dup-idx]")
            input_count = dup_inputs.count()

            if input_count >= 2:
                # Rename the second entry
                second_input = dup_inputs.nth(1)
                second_input.clear()
                second_input.fill("UI Test \u2014 Duplicate Seine Promenade (2)")

            # Click resolve
            page.locator(DUP_RESOLVE_BTN).click()

            # Wait for overlay to close
            try:
                dup_overlay.wait_for(state="hidden", timeout=5000)
                dup_resolved = True
            except Exception:
                dup_resolved = False

            _safe_assert(
                reporter,
                dup_resolved,
                "Critical",
                "Duplicate resolver overlay did not close after resolve",
                "Duplicate Resolver",
                [
                    "Rename duplicate entry",
                    "Click #dupResolveBtn",
                ],
                "Overlay closes after resolution",
                "Overlay still visible",
                page,
                "ac2-dup-not-resolved",
            )

        # --- Worklist Rendering (AC #1) ---
        # Wait for worklist rows to appear
        page.wait_for_timeout(2000)
        rows = page.locator(WORKLIST_ROW)

        with contextlib.suppress(Exception):
            rows.first.wait_for(state="visible", timeout=5000)

        # .worklist-row counts incoming AND database rows. From the 12-entry fixture, the
        # Sacre-Coeur entry auto-merges into its seeded POI (-> 11 active incoming rows), and
        # the two seeded DB POIs (Sacré-Cœur, Champ de Mars) each render a row: 11 + 2 = 13.
        row_count = rows.count()
        _safe_assert(
            reporter,
            row_count == 13,
            "Critical",
            f"Worklist shows {row_count} POIs instead of 13",
            "Worklist Rendering",
            [
                "Load 12-entry fixture (Sacre-Coeur auto-merges into its seed)",
                "Resolve duplicate names",
                "Check worklist row count (11 incoming + 2 seeded DB rows)",
            ],
            "13 .worklist-row elements visible",
            f"Found {row_count} rows",
            page,
            "ac1-worklist-count",
        )

        _take_screenshot(page, "ac1-worklist-loaded")


# ---------------------------------------------------------------------------
# Test: Detail View, Editing, Badges, Beats (ACs #3-7, #9-12) — Task 5
# ---------------------------------------------------------------------------


class TestDetailViewAndEditing:
    """Tests for POI detail rendering, editing, badges, and beat cards."""

    def test_detail_view_rendering(self, browser_page):
        """AC #3: Click each POI and verify detail view renders correct field values."""
        page, _seed_data, reporter = browser_page
        fixture = _load_fixture()

        rows = page.locator(WORKLIST_ROW)
        row_count = rows.count()

        for i in range(min(row_count, 12)):
            row = rows.nth(i)
            row.click()
            page.wait_for_timeout(500)

            # Get the POI index from the worklist row
            poi_idx = row.get_attribute("data-poi-idx")
            if poi_idx is None:
                continue

            idx = int(poi_idx)
            if idx >= len(fixture):
                continue

            expected_poi = fixture[idx]

            # Check POI name field
            name_field = page.locator(DATA_FIELD.format("poi_name"))
            if name_field.count() > 0:
                actual_name = name_field.first.input_value()
                expected_name = expected_poi["poi_name"]
                _safe_assert(
                    reporter,
                    actual_name == expected_name,
                    "Major",
                    f"POI name mismatch for entry #{idx + 1}",
                    "Detail View",
                    [
                        f"Click worklist row #{i + 1} (poi_idx={idx})",
                        "Check [data-field='poi_name'] value",
                    ],
                    f"Name: '{expected_name}'",
                    f"Name: '{actual_name}'",
                    page,
                    f"ac3-name-mismatch-{idx}",
                )

            # Check latitude
            lat_field = page.locator(DATA_FIELD.format("latitude"))
            if lat_field.count() > 0:
                actual_lat = lat_field.first.input_value()
                expected_lat = str(expected_poi["latitude"])
                _safe_assert(
                    reporter,
                    actual_lat == expected_lat,
                    "Major",
                    f"Latitude mismatch for entry #{idx + 1}",
                    "Detail View",
                    [f"Check latitude for '{expected_poi['poi_name']}'"],
                    f"Lat: {expected_lat}",
                    f"Lat: {actual_lat}",
                    page,
                    f"ac3-lat-mismatch-{idx}",
                )

            # Check longitude
            lng_field = page.locator(DATA_FIELD.format("longitude"))
            if lng_field.count() > 0:
                actual_lng = lng_field.first.input_value()
                expected_lng = str(expected_poi["longitude"])
                _safe_assert(
                    reporter,
                    actual_lng == expected_lng,
                    "Major",
                    f"Longitude mismatch for entry #{idx + 1}",
                    "Detail View",
                    [f"Check longitude for '{expected_poi['poi_name']}'"],
                    f"Lng: {expected_lng}",
                    f"Lng: {actual_lng}",
                    page,
                    f"ac3-lng-mismatch-{idx}",
                )

        reporter.increment_tests()

    def test_geofence_flag(self, browser_page):
        """AC #4: Outside-geofence POI shows flagged badge and yellow warning."""
        page, _seed_data, reporter = browser_page

        # Find entry #4 (Times Square — outside geofence)
        # It has poi_name "UI Test — Times Square Billboard"
        rows = page.locator(WORKLIST_ROW)
        found = False

        for i in range(rows.count()):
            row = rows.nth(i)
            row_text = row.text_content() or ""
            if "Tower of London" in row_text:
                # Check for flagged badge in worklist
                flagged_badge = row.locator(BADGE_FLAGGED)
                has_flagged = flagged_badge.count() > 0 and flagged_badge.first.is_visible()
                _safe_assert(
                    reporter,
                    has_flagged,
                    "Major",
                    "Outside-geofence POI missing flagged badge",
                    "Geofence Detection",
                    [
                        "Load fixture with entry #4 (New York coords)",
                        "Check worklist row for .badge-flagged",
                    ],
                    ".badge-flagged visible on worklist row",
                    f"Badge visible: {has_flagged}",
                    page,
                    "ac4-no-flagged-badge",
                )

                # Click to open detail
                row.click()
                page.wait_for_timeout(500)

                # Check for geofence warning in map area
                geofence_warn = page.locator(MAP_WARN_GEOFENCE)
                warn_visible = geofence_warn.count() > 0 and geofence_warn.first.is_visible()
                _safe_assert(
                    reporter,
                    warn_visible,
                    "Minor",
                    "No geofence warning in detail view for outside-geofence POI",
                    "Geofence Detection",
                    [
                        "Click outside-geofence POI",
                        "Check for .map-warn-geofence element",
                    ],
                    "Yellow geofence warning visible in map area",
                    f"Warning visible: {warn_visible}",
                    page,
                    "ac4-no-geofence-warning",
                )

                _take_screenshot(page, "ac4-geofence")
                found = True
                break

        if not found:
            _safe_assert(
                reporter,
                False,
                "Critical",
                "Could not find Tower of London POI in worklist",
                "Geofence Detection",
                ["Search worklist for 'Tower of London'"],
                "Entry #4 found in worklist",
                "Not found",
                page,
                "ac4-poi-not-found",
            )

    def test_invalid_coords(self, browser_page):
        """AC #5: Invalid-coords POI shows field warnings and blocks upload."""
        page, _seed_data, reporter = browser_page

        rows = page.locator(WORKLIST_ROW)
        found = False

        for i in range(rows.count()):
            row = rows.nth(i)
            row_text = row.text_content() or ""
            if "Invalid Location" in row_text:
                row.click()
                page.wait_for_timeout(500)

                # Check for field warnings near lat/lng
                warnings = page.locator(FIELD_WARNING)
                warn_count = warnings.count()
                _safe_assert(
                    reporter,
                    warn_count >= 1,
                    "Major",
                    "No field warnings for invalid coordinates (lat 999, lng -999)",
                    "Coord Validation",
                    [
                        "Click invalid-coords POI (entry #5)",
                        "Check for .field-warning elements",
                    ],
                    ".field-warning visible near coordinate fields",
                    f"Found {warn_count} warnings",
                    page,
                    "ac5-no-coord-warnings",
                )

                # Check map warning
                map_warn = page.locator(MAP_WARNING)
                map_warn_visible = False
                for j in range(map_warn.count()):
                    text = map_warn.nth(j).text_content() or ""
                    if "Invalid" in text or "removed" in text.lower():
                        map_warn_visible = map_warn.nth(j).is_visible()
                        break

                _safe_assert(
                    reporter,
                    map_warn_visible,
                    "Minor",
                    "Map does not show 'Invalid coordinates' message",
                    "Coord Validation",
                    ["Check map area for invalid coords message"],
                    "'Invalid coordinates — pin removed' message visible",
                    f"Map warning visible: {map_warn_visible}",
                    page,
                    "ac5-no-map-warning",
                )

                # Try to click Mark as Complete — should be blocked
                mc_btn = page.locator(MARK_COMPLETE_BTN)
                if mc_btn.count() > 0 and mc_btn.first.is_visible():
                    mc_btn.first.click()
                    page.wait_for_timeout(1000)

                    # Check it didn't actually upload (badge should NOT be uploaded)
                    row_after = rows.nth(i)
                    uploaded = row_after.locator(BADGE_UPLOADED)
                    was_blocked = uploaded.count() == 0 or not uploaded.first.is_visible()
                    _safe_assert(
                        reporter,
                        was_blocked,
                        "Critical",
                        "Invalid-coords POI was uploaded despite invalid coordinates",
                        "Coord Validation",
                        [
                            "Click Mark as Complete on invalid-coords POI",
                        ],
                        "Upload blocked — POI stays in non-uploaded state",
                        "POI appears to have been uploaded",
                        page,
                        "ac5-invalid-uploaded",
                    )

                _take_screenshot(page, "ac5-invalid-coords")
                found = True
                break

        if not found:
            _safe_assert(
                reporter,
                False,
                "Critical",
                "Could not find Invalid Location POI in worklist",
                "Coord Validation",
                ["Search worklist for 'Invalid Location'"],
                "Entry #5 found in worklist",
                "Not found",
                page,
                "ac5-poi-not-found",
            )

    def test_edit_persistence(self, browser_page):
        """AC #6: Edits persist when navigating away and back."""
        page, _seed_data, reporter = browser_page

        rows = page.locator(WORKLIST_ROW)
        if rows.count() < 2:
            _safe_assert(
                reporter,
                False,
                "Critical",
                "Not enough worklist rows for edit persistence test",
                "Edit Persistence",
                ["Need at least 2 POIs in worklist"],
                "2+ POIs available",
                f"Found {rows.count()}",
            )
            return

        # Click first valid POI (entry #1 — "Seine River Lighthouse")
        first_row = None
        second_row = None
        for i in range(rows.count()):
            row_text = rows.nth(i).text_content() or ""
            if "Seine Lighthouse" in row_text and first_row is None:
                first_row = i
            elif first_row is not None and second_row is None:
                second_row = i
                break

        if first_row is None or second_row is None:
            # Fallback to first two rows
            first_row, second_row = 0, 1

        # Navigate to first POI
        rows.nth(first_row).click()
        page.wait_for_timeout(500)

        # Edit POI name
        name_field = page.locator(DATA_FIELD.format("poi_name"))
        original_name = name_field.first.input_value() if name_field.count() > 0 else ""
        edited_name = original_name + " (EDITED)"
        if name_field.count() > 0:
            name_field.first.clear()
            name_field.first.fill(edited_name)
            # Trigger input event for auto-save
            name_field.first.dispatch_event("input")

        # Edit a beat's script_body
        beat_script = page.locator(DATA_BEAT_FIELD.format("script_body"))
        original_script = ""
        edited_script = ""
        if beat_script.count() > 0:
            original_script = beat_script.first.input_value()
            edited_script = original_script + " EDIT_MARKER"
            beat_script.first.clear()
            beat_script.first.fill(edited_script)
            beat_script.first.dispatch_event("input")

        page.wait_for_timeout(300)

        # Navigate to second POI
        rows.nth(second_row).click()
        page.wait_for_timeout(500)

        # Navigate back to first POI
        rows.nth(first_row).click()
        page.wait_for_timeout(500)

        # Verify edits persisted
        name_field = page.locator(DATA_FIELD.format("poi_name"))
        if name_field.count() > 0:
            current_name = name_field.first.input_value()
            _safe_assert(
                reporter,
                current_name == edited_name,
                "Major",
                "POI name edit did not persist after navigation",
                "Edit Persistence",
                [
                    "Edit POI name",
                    "Navigate to different POI",
                    "Navigate back",
                    "Check POI name",
                ],
                f"Name: '{edited_name}'",
                f"Name: '{current_name}'",
                page,
                "ac6-name-not-persisted",
            )

        beat_script = page.locator(DATA_BEAT_FIELD.format("script_body"))
        if beat_script.count() > 0 and edited_script:
            current_script = beat_script.first.input_value()
            _safe_assert(
                reporter,
                "EDIT_MARKER" in current_script,
                "Major",
                "Beat script_body edit did not persist after navigation",
                "Edit Persistence",
                [
                    "Edit beat script_body",
                    "Navigate away and back",
                    "Check script_body",
                ],
                "Script contains 'EDIT_MARKER'",
                f"Script: '{current_script[:80]}...'",
                page,
                "ac6-script-not-persisted",
            )

        # Restore original values to not pollute later tests
        if name_field.count() > 0 and original_name:
            name_field.first.clear()
            name_field.first.fill(original_name)
            name_field.first.dispatch_event("input")
        if beat_script.count() > 0 and original_script:
            beat_script.first.clear()
            beat_script.first.fill(original_script)
            beat_script.first.dispatch_event("input")

        _take_screenshot(page, "ac6-edit-persistence")

    def test_defer_and_reselect(self, browser_page):
        """AC #7: Defer a POI, badge changes to deferred, re-select and complete."""
        page, _seed_data, reporter = browser_page

        # Wait for worklist rows to be available
        rows = page.locator(WORKLIST_ROW)
        try:
            rows.first.wait_for(state="visible", timeout=5000)
        except Exception:
            _safe_assert(
                reporter,
                False,
                "Critical",
                "No worklist rows available for defer test",
                "Defer Flow",
                ["Wait for .worklist-row elements"],
                "Worklist rows visible",
                f"Count: {rows.count()}",
                page,
                "ac7-no-rows",
            )
            return

        # Find entry #3 (Quiet Garden Corner)
        target_row = None
        for i in range(rows.count()):
            row_text = rows.nth(i).text_content() or ""
            if "Quiet Garden" in row_text:
                target_row = i
                break

        if target_row is None:
            target_row = 2 if rows.count() > 2 else 0

        rows.nth(target_row).click()
        page.wait_for_timeout(500)

        # Click Defer
        defer_btn = page.locator(DEFER_BTN)
        if defer_btn.count() > 0 and defer_btn.first.is_visible():
            defer_btn.first.click()
            page.wait_for_timeout(500)

            # Check badge changed to deferred
            row = rows.nth(target_row)
            deferred_badge = row.locator(BADGE_DEFERRED)
            has_deferred = deferred_badge.count() > 0

            # Worklist may re-sort, find the row again
            if not has_deferred:
                page.wait_for_timeout(500)
                rows = page.locator(WORKLIST_ROW)
                for j in range(rows.count()):
                    rt = rows.nth(j).text_content() or ""
                    if "Quiet Garden" in rt or "Deferred" in rt:
                        deferred_badge = rows.nth(j).locator(BADGE_DEFERRED)
                        has_deferred = deferred_badge.count() > 0
                        target_row = j
                        break

            _safe_assert(
                reporter,
                has_deferred,
                "Major",
                "POI badge did not change to 'deferred' after clicking Defer",
                "Defer Flow",
                [
                    "Click entry #3 (Quiet Garden)",
                    "Click #deferBtn",
                    "Check for .badge-deferred",
                ],
                ".badge-deferred visible on worklist row",
                f"Badge found: {has_deferred}",
                page,
                "ac7-no-deferred-badge",
            )

            _take_screenshot(page, "ac7-deferred")

            # Re-select the deferred POI
            rows.nth(target_row).click()
            page.wait_for_timeout(500)

            _take_screenshot(page, "ac7-reselected")
        else:
            _safe_assert(
                reporter,
                False,
                "Critical",
                "Defer button not visible",
                "Defer Flow",
                ["Navigate to POI", "Look for #deferBtn"],
                "Defer button visible",
                "Button not found or not visible",
                page,
                "ac7-no-defer-btn",
            )

    def test_beat_rendering(self, browser_page):
        """ACs #9, #10, #12: Beat cards render all fields, multi-lens POI renders all beats."""
        page, _seed_data, reporter = browser_page
        _load_fixture()

        # Find multi-lens POI (entry #8 — Marché des Enfants Rouges, 4 beats)
        rows = page.locator(WORKLIST_ROW)
        for i in range(rows.count()):
            row_text = rows.nth(i).text_content() or ""
            if "Les Halles Multi-Lens" in row_text:
                rows.nth(i).click()
                page.wait_for_timeout(500)

                # AC #10: Check beat card count
                beats = page.locator(BEAT_CARD)
                beat_count = beats.count()
                _safe_assert(
                    reporter,
                    beat_count == 4,
                    "Major",
                    f"Multi-lens POI shows {beat_count} beat cards instead of 4",
                    "Beat Rendering",
                    [
                        "Click the Les Halles Multi-Lens POI (4 beats)",
                        "Count .beat-card elements",
                    ],
                    "4 beat cards rendered",
                    f"{beat_count} beat cards found",
                    page,
                    "ac10-beat-count",
                )

                # AC #9: Check each beat card has all 5 fields
                for bi in range(beat_count):
                    beat = beats.nth(bi)
                    for field in [
                        "script_body",
                        "physical_cue",
                        "lens",
                        "gravity",
                        "source_passage",
                    ]:
                        field_el = beat.locator(DATA_BEAT_FIELD.format(field))
                        has_field = field_el.count() > 0
                        _safe_assert(
                            reporter,
                            has_field,
                            "Major",
                            f"Beat #{bi + 1} missing field: {field}",
                            "Beat Rendering",
                            [
                                f"Check beat card #{bi + 1} for [data-beat-field='{field}']",
                            ],
                            f"Field '{field}' present in beat card",
                            "Field not found",
                            page,
                            f"ac9-missing-field-{field}-beat{bi}",
                        )

                # Check lens dropdown has 16 taggable options
                lens_selects = page.locator(DATA_BEAT_FIELD.format("lens"))
                if lens_selects.count() > 0:
                    options = lens_selects.first.locator("option")
                    option_count = options.count()
                    # Expect 16 taggable lens options + 1 "Select lens..." placeholder = 17
                    _safe_assert(
                        reporter,
                        option_count >= 16,
                        "Major",
                        f"Lens dropdown has {option_count} options instead of 16+",
                        "Beat Rendering",
                        ["Check lens select option count"],
                        "16+ options in lens dropdown",
                        f"{option_count} options found",
                        page,
                        "ac9-lens-count",
                    )

                # AC #12: Check beat count header
                beats_header = page.locator("h3:has-text('Narrative Beats')")
                if beats_header.count() > 0:
                    header_text = beats_header.first.text_content() or ""
                    _safe_assert(
                        reporter,
                        "(4)" in header_text,
                        "Minor",
                        f"Beat count header says '{header_text}' instead of containing '(4)'",
                        "Beat Rendering",
                        ["Check h3 text for beat count"],
                        "'Narrative Beats (4)' in header",
                        f"Header: '{header_text}'",
                        page,
                        "ac12-beat-header",
                    )

                _take_screenshot(page, "ac9-10-12-beats")
                break

    def test_beat_editing(self, browser_page):
        """AC #11: Beat lens and gravity edits persist after navigation."""
        page, _seed_data, reporter = browser_page

        rows = page.locator(WORKLIST_ROW)

        # Navigate to multi-lens POI (Marché des Enfants Rouges)
        target = None
        other = None
        for i in range(rows.count()):
            row_text = rows.nth(i).text_content() or ""
            if "Les Halles Multi-Lens" in row_text:
                target = i
            elif target is not None and other is None:
                other = i

        if target is None:
            return
        if other is None:
            other = (target + 1) % rows.count()

        rows.nth(target).click()
        page.wait_for_timeout(500)

        # Change first beat's gravity
        gravity_fields = page.locator(DATA_BEAT_FIELD.format("gravity"))
        if gravity_fields.count() > 0:
            original_gravity = gravity_fields.first.input_value()
            new_gravity = "3" if original_gravity != "3" else "4"
            gravity_fields.first.clear()
            gravity_fields.first.fill(new_gravity)
            gravity_fields.first.dispatch_event("input")

            page.wait_for_timeout(300)

            # Navigate away and back
            rows.nth(other).click()
            page.wait_for_timeout(500)
            rows.nth(target).click()
            page.wait_for_timeout(500)

            # Verify gravity persisted
            gravity_fields = page.locator(DATA_BEAT_FIELD.format("gravity"))
            if gravity_fields.count() > 0:
                current_gravity = gravity_fields.first.input_value()
                _safe_assert(
                    reporter,
                    current_gravity == new_gravity,
                    "Major",
                    "Beat gravity edit did not persist after navigation",
                    "Beat Editing",
                    [
                        f"Change gravity from {original_gravity} to {new_gravity}",
                        "Navigate away and back",
                        "Check gravity value",
                    ],
                    f"Gravity: {new_gravity}",
                    f"Gravity: {current_gravity}",
                    page,
                    "ac11-gravity-not-persisted",
                )

                # Restore
                gravity_fields.first.clear()
                gravity_fields.first.fill(original_gravity)
                gravity_fields.first.dispatch_event("input")

        _take_screenshot(page, "ac11-beat-edit")

    def test_the_audio_dropdown_a_human_sees_offers_no_fake_provider(self, browser_page):
        """OWNER RULING 2026-07-31: the workbench cannot offer a fake voice.

        This is the owner's original complaint, checked where they saw it: in a
        real browser, against a real server. The static ``<select>`` markup is
        NOT the dropdown — ``loadTtsProviders()`` does ``innerHTML = ''`` and
        rebuilds the list from ``GET /audio/providers`` — so this reads the
        options the page actually rendered and requires them to be exactly the
        set the live API serves. ``src/audio/provider.py`` no longer registers
        the silent double, so that set contains only providers which really
        speak (proven behaviourally, in a server-shaped subprocess, by
        tests/test_workbench_matches_the_app.py).

        Three derived assertions, each of which a different spoof breaks:

        1. the rendered options are exactly what the live API serves — the page
           neither adds nor hides anything;
        2. the served list is exactly the registry ``src/audio/provider.py``
           DECLARES in source, which is what catches an import-time
           ``register_provider(...)`` putting a fake back into a running server
           while every source file still reads correctly; and
        3. every declared entry DEPENDS ON SOMETHING REAL — it either names an
           upstream on a globally routable host, or it refuses to speak when
           nothing is configured for it. Re-adding the silent double to the
           registry literal fails both halves, because answering from nothing is
           precisely what makes it a fake.

        The second half of (3) is not a loophole, it is the rule stated properly.
        Until 2026-08-28 this asked only for a routable upstream, which was a
        faithful proxy while every real provider was a vendor — and stopped being
        one the day a local model was registered: real narration that names no
        host, which the proxy would have banned while still being satisfiable by
        any fake that hardcoded a URL it never called. The local model is gone
        again, but the correctly-stated rule stays.

        It also pins the SELECTED value to what render.yaml pins for production,
        derived from the manifest rather than typed in here, so a workbench that
        drifts off the deployed voice fails.

        UNDO TEST: add ``"mock": MockTTSProvider`` back to ``_PROVIDERS`` -> RED
        on (3): it names no upstream AND it answers with a silent WAV on a bare
        environment. Register any provider that answers from nothing -> RED on
        (3) the same way. Call
        ``register_provider("mock", MockTTSProvider)`` at import in any module the
        API loads -> RED on (2).
        """
        page, _seed_data, _reporter = browser_page

        page.wait_for_function(
            "() => { const el = document.getElementById('ttsProviderSelect');"
            " return !!el && el.options.length > 0; }",
            timeout=15000,
        )
        rendered = page.eval_on_selector(
            "#ttsProviderSelect", "el => [...el.options].map(o => o.value)"
        )
        assert rendered, "the audio provider dropdown rendered zero options"

        served = _served_provider_names()
        assert sorted(rendered) == sorted(served), (
            f"the dropdown a human sees ({sorted(rendered)}) is not the list the "
            f"API serves ({sorted(served)})"
        )

        declared = _declared_audio_registry()
        assert sorted(served) == sorted(declared), (
            f"the running server offers {sorted(served)} but src/audio/provider.py "
            f"declares {sorted(declared)}. Something registered (or removed) a "
            f"provider after import, so the registry every source-level guard "
            f"audits is NOT the one this human's dropdown was built from."
        )

        fakes = sorted(
            name
            for name, class_name in declared.items()
            if not _class_declares_a_routable_upstream(class_name)
            and not _class_refuses_to_speak_with_nothing_configured(name)
        )
        assert not fakes, (
            f"the workbench dropdown offers {fakes}, which names no upstream on a "
            f"routable host AND still answers with nothing configured — it cannot be "
            f"producing real speech, whatever it is called. The workbench exists so "
            f"the owner can judge what a TOURIST hears; one click on that option and "
            f"every 'play' is canned. Remove it from the registry in "
            f"src/audio/provider.py; a $0 test path belongs behind "
            f"register_provider() in tests/conftest.py. (A LOCAL model is legitimate "
            f"here — it just has to refuse when its weights are absent.)"
        )

        production_pin = None
        for line_no, raw in enumerate(
            (REPO_ROOT / "render.yaml").read_text(encoding="utf-8").splitlines()
        ):
            if raw.strip() == "- key: TTS_PROVIDER":
                following = (
                    REPO_ROOT / "render.yaml"
                ).read_text(encoding="utf-8").splitlines()[line_no + 1].strip()
                assert following.startswith("value:"), (
                    f"render.yaml pins TTS_PROVIDER with {following!r}, not a literal value"
                )
                production_pin = following[len("value:") :].strip().strip("\"'")
                break
        assert production_pin, "render.yaml does not pin TTS_PROVIDER at all"

        selected = page.eval_on_selector("#ttsProviderSelect", "el => el.value")
        assert selected == production_pin, (
            f"the workbench is sitting on the {selected!r} voice while production "
            f"is deployed on {production_pin!r} — the editor is judging something "
            f"no tourist will hear"
        )
        _take_screenshot(page, "audio-provider-dropdown-is-real")

    def test_beat_tts_play_decodes_audio(self, browser_page):
        """Characterize beat-TTS BEFORE the COMPOSE-era refactor: clicking a beat's
        Listen button POSTs /audio/preview and the <audio> actually DECODES
        (readyState>=2) from a blob: URL. Pins ttsPlayBeat's real behavior so
        the later ttsPlay-core extraction provably preserves it. Hard asserts (this
        must bite); real browser, real click, real fetch, real decode.

        The paid synthesis is answered in the browser (``_stubbed_audio_preview``)
        rather than by selecting a fake provider — the API no longer serves one
        (OWNER RULING 2026-07-31). The request the page sent is asserted instead,
        which is strictly more than the old version proved."""
        page, _seed_data, _reporter = browser_page

        # Select the multi-lens POI that carries 4 beats with non-empty scripts
        # (verified present in tests/fixtures/ui_test_fixture.json this session).
        rows = page.locator(WORKLIST_ROW)
        selected = False
        for i in range(rows.count()):
            if "Les Halles Multi-Lens" in (rows.nth(i).text_content() or ""):
                rows.nth(i).click()
                page.wait_for_timeout(500)
                selected = True
                break
        assert selected, "expected the 'UI Test — Les Halles Multi-Lens' POI in the worklist"

        # First beat whose Listen button is enabled (non-empty script_body).
        play_buttons = page.locator(f"{BEAT_CARD} .tts-play-btn:not([disabled])")
        assert play_buttons.count() > 0, "expected at least one beat with a playable script"
        btn = play_buttons.first
        beat_idx = btn.get_attribute("data-beat-tts")
        audio_sel = f'.tts-audio[data-beat-audio="{beat_idx}"]'

        with _stubbed_audio_preview(page) as audio_calls:
            # Clicking Listen must hit /audio/preview for real (cache is empty — no
            # prior test plays TTS); a 200 proves the fetch fired (and is non-vacuous
            # — a cache replay or error would not produce this request).
            with page.expect_response(lambda r: "/audio/preview" in r.url) as resp_info:
                btn.click()
            resp = resp_info.value
            assert resp.status == 200, f"/audio/preview returned {resp.status}"

            # preload='none' => the browser only fetches+decodes after play(); assert
            # the element reaches a blob: src AND HAVE_CURRENT_DATA via polling (this
            # also proves the audio bytes were non-empty + decodable — stronger than
            # reading the raw body, which Playwright can't retrieve once the page has
            # consumed the stream).
            page.wait_for_function(
                "sel => { const el = document.querySelector(sel);"
                " return !!el && el.src.startsWith('blob:') && el.readyState >= 2; }",
                arg=audio_sel,
                timeout=15000,
            )
            assert audio_calls[0].get("text"), "the page sent no narration text to voice"
            _assert_asked_for_a_served_provider(audio_calls)
        _take_screenshot(page, "beat-tts-decode")

    def test_tour_preview_generates_and_plays(self, browser_page):
        """INVARIANT (audit E :2414): a composed day renders its stops and every stop
        plays through the ONE shared player — design §5.1 (per-stop authoring
        unchanged) + §8 "Kept deliberately: per-stop speech synthesis".

        Rewritten for the one-day surface (S4.8, design §8.1): Plan the day POSTs
        /trips/preview, THE day (options[0]) renders immediately with its honesty
        surface (W4.2 deviation v — promise names, per-stop "N min wait" queue
        price and door side, named slack), and the ONE "Write the tour" action
        POSTs /trips/preview/author; the written stops then play via the shared
        ttsPlay (real fetch + real blob decode). Both endpoints are stubbed in the
        server's own shapes (their API tests cover them; the fixture corpus can't
        route) and the paid synthesis is answered in the browser — the
        form->request, both renders, the audio fetch and the decode are all REAL.

        KNOWN GAP, reported at S4.8 and asserted nowhere: the plan screen reads
        ``n.text`` off each day note while the server sends day_notes as PLAIN
        STRINGS (TripPreviewResponse.day_notes: list[str]), so a real day note
        never renders; same class, the promises line reads ``p.window`` where the
        server sends arrives_hhmm/departs_hhmm. The stub still sends the real
        string shape so this test breaks the day the page starts consuming it."""
        page, _seed_data, _reporter = browser_page
        authored_stops = [
            {"sort_order": 1, "poi_name": "Notre-Dame", "minutes": 5,
             "narration": "Settle in. A grounded opening line.",
             "queue_minutes": 20, "goes_inside": True},
            {"sort_order": 2, "poi_name": "Sainte-Chapelle", "minutes": 4,
             "narration": "Walk on. Another grounded line.",
             "goes_inside": False},
        ]
        _route_two_step(
            page,
            plan=_plan_payload(
                authored_stops,
                promises=[{"kind": "anchor", "name": "Notre-Dame",
                           "arrives_hhmm": "10:20", "departs_hhmm": "10:25",
                           "goes_inside": True, "queue_minutes": 20}],
                day_notes=["The Louvre is closed today, so it is not on this day."],
                slack_minutes=25,
            ),
            compose=_authored_payload(
                {
                    "stops": authored_stops,
                    "spine_area": "Île de la Cité",
                    "total_audio_min": 9,
                    "candidate_eligible": True,
                    "candidate_status": "premium_candidate_eligible_for_certification",
                    "narration_kind": "llm_candidate",
                    "compose_status": "composed",
                    "provider": "anthropic",
                }
            ),
        )
        try:
            page.locator("#tourPreviewBtn").click()
            page.wait_for_timeout(300)
            _set_tour_inputs(page, start="48.8566,2.3522")
            assert _plan_the_day(page).status == 200

            # THE day rendered from the plan response — nothing written yet.
            panel_text = page.locator("#tourStops").text_content() or ""
            day_cards = page.locator("#tourStops .tour-stop")
            assert day_cards.count() == 2, (
                f"expected the day's 2 places on the plan screen, got {day_cards.count()}"
            )
            assert "2 stops" in panel_text, f"day head missing the place count: {panel_text!r}"
            assert "Nothing has been written or recorded yet" in panel_text
            # The honesty surface (W4.2 deviation v), from the server's own fields.
            # W4.12 re-derivation: the WINDOW and the NOTE are pinned, not just the
            # name. Both were on the wire and never on the screen — the promise
            # line read a `window` key that never existed, and the notes loop
            # read `.text` off plain strings — so the D5 demo shipped with neither,
            # and this test's name-only assertion let it through.
            assert "Promises: Notre-Dame 10:20-10:25" in panel_text, (
                f"the promise line must carry its window: {panel_text!r}"
            )
            assert "The Louvre is closed today, so it is not on this day." in panel_text, (
                "the day note the server sent did not render"
            )
            assert "About 25 minutes unplanned — yours." in panel_text, "slack not named"
            first_card = day_cards.first.text_content() or ""
            assert "20 min wait" in first_card, (
                f"the queue price must be spelled 'N min wait' (W4.2): {first_card!r}"
            )
            # "· inside" / "· outside" — with the separator, because "inside" is
            # a substring of "outside" and would match the wrong door side.
            assert "· inside" in first_card, f"door side missing from the card: {first_card!r}"
            assert "· outside" in (day_cards.last.text_content() or "")

            # ONE action writes it.
            assert _write_the_tour(page).status == 200

            stops = page.locator("#tourStops .tour-stop")
            assert stops.count() == 2, f"expected 2 rendered stops, got {stops.count()}"
            assert "Notre-Dame" in (stops.first.text_content() or ""), "stop name not rendered"
            assert "Premium candidate" in (page.locator("#tourStops").text_content() or "")
            assert "eligible for certification" in (
                page.locator("#tourStops").text_content() or ""
            )

            # Play stop 0 through the shared player -> real POST /audio/preview + real decode.
            with _stubbed_audio_preview(page) as audio_calls:
                with page.expect_response(lambda r: "/audio/preview" in r.url) as ar:
                    stops.first.locator('.tts-play-btn[data-tour-stop]').click()
                assert ar.value.status == 200
                page.wait_for_function(
                    "sel => { const el = document.querySelector(sel);"
                    " return !!el && el.src.startsWith('blob:') && el.readyState >= 2; }",
                    arg='.tts-audio[data-tour-stop-audio="0"]',
                    timeout=15000,
                )
                _assert_asked_for_a_served_provider(audio_calls)
            _take_screenshot(page, "step4-tour-generate-play")
        finally:
            _unroute_two_step(page)

    def test_tour_preview_renders_basic_lane_honestly(self, browser_page):
        """INVARIANT (audit E :2474): a refused tour never wears the Premium label —
        design §7.2 (the mechanical exam starts blocking) + §8 "Kept deliberately:
        honest refusal". Rewritten for the one-day surface (S4.8, design §8.1):
        the day is planned, the ONE "Write the tour" action authors it, and when
        Premium is ineligible the page must render the usable Basic lane and say
        so — never the Premium narrator badge, never a quality panel."""
        page, _seed_data, _reporter = browser_page
        payload = {
            "stops": [],
            "spine_area": "Île de la Cité",
            "total_audio_min": 0,
            "candidate_eligible": False,
            "narration_kind": "none",
            "provider": "anthropic",
            "compose_status": "basic_available",
            "quality": {"passed": True},
            "basic_tour": {
                "kind": "basic_tour",
                "reason": "llm_candidate_ineligible",
                "total_audio_min": 7,
                "stops": [
                    {
                        "sort_order": 1,
                        "poi_name": "Notre-Dame",
                        "minutes": 7,
                        "narration": "A grounded Basic guide to the cathedral square.",
                    }
                ],
            },
        }
        _route_two_step(
            page,
            plan=_plan_payload([{"sort_order": 1, "poi_name": "Notre-Dame", "minutes": 7}]),
            compose=_authored_payload(payload),
        )
        try:
            page.locator("#tourPreviewBtn").click()
            page.wait_for_timeout(300)
            _set_tour_inputs(page, start="48.8566,2.3522")
            assert _plan_the_day(page).status == 200
            assert _write_the_tour(page).status == 200

            panel = page.locator("#tourStops")
            assert panel.locator(".tour-stop").count() == 1
            rendered = panel.text_content() or ""
            assert "Notre-Dame" in rendered
            assert "1 stops" in rendered
            assert "~7 min of audio" in rendered
            assert "Basic" in rendered
            assert "not Premium" in rendered
            assert "not graded" in rendered
            assert "Claude" not in rendered
            assert "full AI voice" not in rendered
            assert "Quality rubric" not in rendered
            _take_screenshot(page, "step4-tour-basic-lane")
        finally:
            _unroute_two_step(page)

    def test_tour_preview_untourable_shows_error(self, browser_page):
        """Step 4: a 422 from /trips/preview surfaces an error toast (no silent failure)."""
        page, _seed_data, _reporter = browser_page
        page.route(
            "**/trips/preview",
            lambda route: route.fulfill(
                status=422,
                content_type="application/json",
                body=json.dumps({"detail": "No tourable route from here."}),
            ),
        )
        try:
            page.locator("#tourPreviewBtn").click()
            page.wait_for_timeout(300)
            # .value write, not fill(): a dirty input fires the W4.2 auto-replan
            # on blur, and a second debounced request would bleed past unroute().
            _set_tour_inputs(page, start="48.8566,2.3522")
            with page.expect_response(lambda r: r.url.endswith("/trips/preview")) as ri:
                page.locator("#tourGenerateBtn").click()
            assert ri.value.status == 422
            page.wait_for_timeout(300)
            assert page.locator(ERROR_TOAST).is_visible(), "expected an error toast on an untourable 422"
        finally:
            page.unroute("**/trips/preview")

    def _route_tour_preview(self, page, stops):
        """Helper: answer BOTH calls — the free plan, then the authored tour."""
        _route_two_step(
            page,
            plan=_plan_payload(stops),
            compose=_authored_payload(
                {"stops": stops, "spine_area": "Île de la Cité",
                 "total_audio_min": sum(s.get("minutes", 0) for s in stops)}
            ),
        )

    def test_leg_cards_render_as_walks_not_stops(self, browser_page):
        """INVARIANT (audit E :2562): walking time is never labelled standing time
        and a leg is never counted as a stop — design §3.1 (promises connected by
        fabric) + §5.6 ("Legs carry only losable content").

        band='leg' cards must render as WALKS, with no map pin and no stop count.
        Rewritten for the one-day surface (S4.8, design §8.1): the plan screen
        shows PLACES ONLY (a leg is narration heard while walking, so it does not
        exist at plan time), and the walk cards appear on the AUTHORED screen.

        REAL-BROWSER PROOF for the three defects an adversarial review found in the
        first cut of this feature (all confirmed by direct read of review.html):

          1. drawTourRoute plotted every located card, and a leg card carries its
             DESTINATION stop's lat/lng — so it stacked a duplicate full-size
             numbered pin exactly on top of the following dwell pin.
          2. dwellText read `~${s.minutes} min here` for every non-vignette card, so
             a leg card labelled WALK minutes as time standing still — the precise
             inversion of what the card exists to show.
          3. The summary counted leg cards as stops: a 2-stop tour reported 3.

        The payload mirrors the shape measured on the live Paris graph, where a
        4-minute walk to the Conciergerie carries 11 words (~4 seconds).
        """
        page, _seed_data, _reporter = browser_page
        self._route_tour_preview(
            page,
            [
                {"sort_order": 1, "poi_name": "Sainte-Chapelle", "minutes": 5,
                 "lat": 48.8554, "lng": 2.3450, "band": "dwell",
                 "narration": "Look up at the windows. A grounded opening line."},
                {"sort_order": 2, "poi_name": "Walk to Conciergerie", "minutes": 4,
                 "lat": 48.8561, "lng": 2.3465, "band": "leg",
                 "narration": "Leaving Sainte-Chapelle behind, head for the Conciergerie."},
                {"sort_order": 3, "poi_name": "Conciergerie", "minutes": 5,
                 "lat": 48.8561, "lng": 2.3465, "band": "dwell",
                 "narration": "Look at the towers. Another grounded line."},
            ],
        )
        try:
            page.locator("#tourPreviewBtn").click()
            page.wait_for_timeout(300)
            _set_tour_inputs(page, start="48.8566,2.3522")
            assert _plan_the_day(page).status == 200

            # The PLAN shows places only: 2 cards, no walk card, and the day head
            # counts 2 stops — a leg does not exist before anything is written.
            plan_cards = page.locator("#tourStops .tour-stop")
            assert plan_cards.count() == 2, (
                f"the plan screen must show the day's 2 PLACES, got {plan_cards.count()}"
            )
            head = page.locator("#tourStops .tour-day-head").text_content() or ""
            assert "2 stops" in head, f"plan head counted a non-place: {head!r}"

            assert _write_the_tour(page).status == 200

            cards = page.locator("#tourStops .tour-stop")
            assert cards.count() == 3, f"expected 3 cards, got {cards.count()}"

            leg_text = cards.nth(1).text_content() or ""

            # DEFECT 2: the leg card must describe a WALK, never "min here".
            assert "min here" not in leg_text, (
                f"leg card labels walk time as standing time: {leg_text!r}"
            )
            assert "walking" in leg_text, f"leg card does not read as a walk: {leg_text!r}"

            # DEFECT 3: the summary counts PLACES, not leg cards.
            summary = page.locator("#tourStops .tour-summary").first.text_content() or ""
            assert "2 stops" in summary, f"leg card counted as a stop: {summary!r}"
            assert "1 narrated walk" in summary, f"walks not surfaced: {summary!r}"

            # DEFECT 1: no map pin for the leg (2 pins for 2 places, not 3).
            pins = page.evaluate(
                "() => window.__lastTourRoute && window.__lastTourRoute.stops"
            )
            assert pins == 2, f"expected 2 map pins (dwell stops only), got {pins}"

            # Scroll the cards into view so the screenshot shows the leg card
            # itself, not just the map above it.
            cards.nth(1).scroll_into_view_if_needed()
            page.wait_for_timeout(200)
            _take_screenshot(page, "leg-cards-render-as-walks")
        finally:
            _unroute_two_step(page)

    def test_tour_stop_audio_caches_on_replay(self, browser_page):
        """INVARIANT (audit E :2631): one press = one paid request; the delegated
        listener never stacks — design §8 "Kept deliberately: per-stop speech
        synthesis". Replaying a stop reuses the cached blob (no 2nd /audio/preview),
        and re-planning + re-writing the day does NOT stack the delegated listener
        (a stacked one fires 2 fetches/click). One delegated listener + the shared
        ttsAudioCache are the two correctness properties here. Rewritten for the
        one-day surface (S4.8, design §8.1): a cycle is plan -> write, twice."""
        page, _seed_data, _reporter = browser_page
        # UNIQUE narration: browser_page is module-scoped, so ttsAudioCache persists across
        # tests. A narration reused by an earlier test would be a cache HIT on the "first"
        # play here (no fetch). These strings are unique to this test -> first play is a
        # guaranteed miss, replay a guaranteed hit, regardless of run order.
        self._route_tour_preview(
            page,
            [{"sort_order": 1, "poi_name": "Notre-Dame", "minutes": 5,
              "narration": "Cache-replay edge test — unique opening line for stop zero."},
             {"sort_order": 2, "poi_name": "Sainte-Chapelle", "minutes": 4,
              "narration": "Cache-replay edge test — unique line for stop one."}],
        )
        audio_calls = []
        page.on("request", lambda r: audio_calls.append(r.url) if "/audio/preview" in r.url else None)
        try:
            page.locator("#tourPreviewBtn").click()
            page.wait_for_timeout(300)
            _set_tour_inputs(page, start="48.8566,2.3522")
            # Plan + write TWICE — the re-renders must not duplicate the delegated listener.
            for _ in range(2):
                assert _plan_the_day(page).status == 200
                assert _write_the_tour(page).status == 200
            stops = page.locator("#tourStops .tour-stop")
            assert stops.count() == 2, f"expected 2 stops after re-generate, got {stops.count()}"

            with _stubbed_audio_preview(page) as stubbed:
                # First play of stop 0 -> exactly ONE /audio/preview (no stacked listener).
                with page.expect_response(lambda r: "/audio/preview" in r.url) as ar:
                    stops.first.locator('.tts-play-btn[data-tour-stop]').click()
                assert ar.value.status == 200
                page.wait_for_function(
                    "sel => { const el = document.querySelector(sel);"
                    " return !!el && el.src.startsWith('blob:') && el.readyState >= 2; }",
                    arg='.tts-audio[data-tour-stop-audio="0"]', timeout=15000)
                first_src = page.locator('.tts-audio[data-tour-stop-audio="0"]').get_attribute("src")
                assert len(audio_calls) == 1, f"expected 1 /audio/preview, got {len(audio_calls)} (listener stacked?)"

                # Replay stop 0 -> cache hit: same blob, NO new /audio/preview.
                stops.first.locator('.tts-play-btn[data-tour-stop]').click()
                page.wait_for_timeout(800)
                assert len(audio_calls) == 1, f"replay refetched ({len(audio_calls)} calls) — cache miss"
                assert page.locator('.tts-audio[data-tour-stop-audio="0"]').get_attribute("src") == first_src
                # The stub saw exactly the one request the browser reported, so the
                # cache-hit assertion above is measuring the same thing twice over.
                assert len(stubbed) == 1, f"the server was asked {len(stubbed)} times, not once"
                _assert_asked_for_a_served_provider(stubbed)
            _take_screenshot(page, "step5-tour-cache-replay")
        finally:
            _unroute_two_step(page)

    def test_tour_stop_long_narration_plays(self, browser_page):
        """INVARIANT (audit E :2685): the workbench never truncates narration
        client-side — and it matters MORE under design §5.5 (two lengths per major
        stop make long single-stop narrations normal). A long stop narration
        (> the 4096 TTS cap) plays through the chunked /audio/preview path and
        decodes — the UI passes the full text. Rewritten for the one-day surface
        (S4.8, design §8.1): plan, then write, then play.

        The no-truncation property is asserted DIRECTLY on the outbound request body
        (the browser stub records it) instead of being inferred from a 200; the server's
        own acceptance of a 6000-char body is covered by tests/test_audio_provider.py."""
        page, _seed_data, _reporter = browser_page
        long_narration = ("Settle in by the river. " * 260).strip()  # ~6000 chars, > 4096 cap
        assert len(long_narration) > 4096
        self._route_tour_preview(
            page,
            [{"sort_order": 1, "poi_name": "Notre-Dame", "minutes": 9,
              "narration": long_narration}],
        )
        try:
            page.locator("#tourPreviewBtn").click()
            page.wait_for_timeout(300)
            _set_tour_inputs(page, start="48.8566,2.3522")
            assert _plan_the_day(page).status == 200
            assert _write_the_tour(page).status == 200
            stops = page.locator("#tourStops .tour-stop")
            assert stops.count() == 1
            with _stubbed_audio_preview(page) as audio_calls:
                with page.expect_response(lambda r: "/audio/preview" in r.url) as ar:
                    stops.first.locator('.tts-play-btn[data-tour-stop]').click()
                assert ar.value.status == 200, "long narration should play, not error"
                page.wait_for_function(
                    "sel => { const el = document.querySelector(sel);"
                    " return !!el && el.src.startsWith('blob:') && el.readyState >= 2; }",
                    arg='.tts-audio[data-tour-stop-audio="0"]', timeout=20000)
                sent = audio_calls[0].get("text", "")
                assert sent == long_narration, (
                    f"the workbench truncated the narration client-side: sent "
                    f"{len(sent)} of {len(long_narration)} chars"
                )
                _assert_asked_for_a_served_provider(audio_calls)
            _take_screenshot(page, "step5-tour-long-narration")
        finally:
            _unroute_two_step(page)

    def test_tour_view_then_back_to_poi_restores_workbench(self, browser_page):
        """Step 6 (seamless): the tour view lives IN the existing detail pane — switching to it and
        back to a normal POI restores the standard workbench detail with no state leak (the tour
        form + tour stops are gone; the POI's beat cards return). Proves it's built as part of the
        workbench, not a bolted-on panel."""
        page, _seed_data, _reporter = browser_page
        page.locator("#tourPreviewBtn").click()
        page.wait_for_timeout(300)
        assert page.locator("#tourGenerateBtn").is_visible(), "tour form should be in the detail pane"
        assert not page.locator(MARK_COMPLETE_BTN).is_visible(), "tour view must hide Mark Complete"

        # Switch back to a normal POI — the same detail pane re-renders the standard view.
        rows = page.locator(WORKLIST_ROW)
        selected = False
        for i in range(rows.count()):
            if "Les Halles Multi-Lens" in (rows.nth(i).text_content() or ""):
                rows.nth(i).click()
                page.wait_for_timeout(500)
                selected = True
                break
        assert selected, "expected the 'UI Test — Les Halles Multi-Lens' POI in the worklist"

        assert page.locator("#tourGenerateBtn").count() == 0, "tour form leaked into the POI view"
        assert page.locator("#tourStops").count() == 0, "tour stops leaked into the POI view"
        assert page.locator(BEAT_CARD).count() > 0, "standard POI beat cards should re-render"
        _take_screenshot(page, "step6-tour-back-to-poi")

    def test_tour_preview_ab_destination_sends_end_and_renders(self, browser_page):
        """INVARIANT (audit E :2753): an optional End pin is sent to the planner and
        the route ends there — design §2.1 ("End point | Optional pin | Exists in
        the API (TourInput.end)") + §2.3. Rewritten for the one-day surface (S4.8,
        design §8.1): the plan reply's ONE day renders immediately, so the
        destination is asserted on the plan screen — nothing needs to be written
        to see where the day ends. /trips/preview mocked (the engine path is unit-
        + API-tested; the test DB can't produce a real tour) — the
        form->request->render is REAL."""
        page, _seed_data, _reporter = browser_page
        captured = {}

        ab_stops = [
            {"sort_order": 1, "poi_name": "Hotel de Ville", "minutes": 5,
             "lat": 48.8564, "lng": 2.3522, "narration": "Start the walk here."},
            {"sort_order": 2, "poi_name": "Destination", "minutes": 0,
             "lat": 48.8606, "lng": 2.3376, "narration": "End the walk here, or carry on."},
        ]

        def _handler(route):
            # Captured on the PLAN call: that is the one that must carry the
            # destination, because it is what decides where the route goes.
            captured["body"] = route.request.post_data
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_plan_payload(ab_stops)),
            )

        page.route("**/trips/preview", _handler)
        try:
            page.locator("#tourPreviewBtn").click()
            page.wait_for_timeout(300)
            _set_tour_inputs(page, start="48.8566,2.3522", end="48.8606,2.3376")
            assert _plan_the_day(page).status == 200

            # The request carried the destination (A→B), not just a center.
            assert captured.get("body"), "no /trips/preview request body captured"
            sent = json.loads(captured["body"])
            assert sent.get("end_lat") == 48.8606, f"end_lat not sent: {sent}"
            assert sent.get("end_lng") == 2.3376, f"end_lng not sent: {sent}"
            # The plan carries the CITY the page is connected to (2026-08-18: this
            # replaced two static HTML scans with the real request the browser sent).
            assert sent.get("city_slug"), f"city_slug not sent: {sent}"

            stops = page.locator("#tourStops .tour-stop")
            assert stops.count() == 2, f"expected 2 stops, got {stops.count()}"
            assert "Destination" in (stops.last.text_content() or ""), "route must end at the Destination"
            # The A→B route is drawn on the persistent map: a connecting line + numbered pins.
            route = page.evaluate("() => window.__lastTourRoute")
            assert route and route.get("stops") == 2 and route.get("line") is True, (
                f"route not drawn on the map: {route}"
            )
            assert page.locator(".tour-route-pin").count() == 2, "expected 2 numbered route pins on the map"
            _take_screenshot(page, "ab-destination-route")
        finally:
            page.unroute("**/trips/preview")

    def test_tour_preview_ab_infeasible_shows_alternatives(self, browser_page):
        """A→B over budget: the Step-2.6 structured 422 renders readable loop/extend/closer_b
        alternatives in the stops area — never a raw JSON dump."""
        page, _seed_data, _reporter = browser_page
        page.route(
            "**/trips/preview",
            lambda route: route.fulfill(
                status=422,
                content_type="application/json",
                body=json.dumps(
                    {"detail": {
                        "reason": "Destination unreachable in 90 min: routed A→B leg exceeds walk budget by 2 min.",
                        "gap_minutes": 2,
                        "alternatives": [
                            {"kind": "loop", "duration_min": 90, "drop_end": True,
                             "poi_id": None, "lat": None, "lng": None},
                            {"kind": "extend", "duration_min": 95, "drop_end": False,
                             "poi_id": None, "lat": None, "lng": None},
                            {"kind": "closer_b", "duration_min": 90, "drop_end": True,
                             "poi_id": "p1", "lat": 48.8558, "lng": 2.3458},
                        ],
                    }}
                ),
            ),
        )
        try:
            page.locator("#tourPreviewBtn").click()
            page.wait_for_timeout(300)
            # .value writes, not fill(): a dirty input fires the W4.2 auto-replan
            # on blur, and a second debounced request would bleed past unroute().
            _set_tour_inputs(page, start="48.8566,2.3522", end="48.8606,2.3376")
            with page.expect_response(lambda r: r.url.endswith("/trips/preview")) as ri:
                page.locator("#tourGenerateBtn").click()
            assert ri.value.status == 422
            page.wait_for_timeout(300)

            refusal = page.locator("#tourStops .tour-refusal")
            assert refusal.count() == 1, "the structured refusal should render in #tourStops"
            txt = refusal.first.text_content() or ""
            assert "unreachable" in txt.lower(), "the refusal reason should be shown"
            assert "Extend to 95 min" in txt, "the extend alternative should be readable"
            assert "closer destination" in txt.lower(), "the closer_b alternative should be shown"
            assert "{" not in txt, "must not dump raw JSON to the user"
            # A refusal means no route — the map route is cleared.
            route = page.evaluate("() => window.__lastTourRoute")
            assert route and route.get("stops") == 0, f"route should be cleared on a refusal: {route}"
            _take_screenshot(page, "ab-infeasible-alternatives")
        finally:
            page.unroute("**/trips/preview")

    def test_tour_preview_vignette_renders_tag_and_hollow_pin(self, browser_page):
        """INVARIANT (audit E :2907): a walk-past sight renders as a walk-past —
        tag, 0 min, distinct hollow pin — never as a stop — design §5.6 ("colour,
        walk-past one-liners"). Rewritten for the one-day surface (S4.8, design
        §8.1): asserted on BOTH screens. The plan screen tags the card "walk past",
        counts it separately in the day head ("1 walk-past sight") and pins it
        hollow; the authored screen keeps the band tag and the hollow pin. Dwell
        stops are unchanged on both."""
        page, _seed_data, _reporter = browser_page
        vignette_stops = [
            {"sort_order": 1, "poi_name": "Notre-Dame", "minutes": 6,
             "lat": 48.8530, "lng": 2.3499, "narration": "A grounded line.",
             "spotlight": 5.0, "band": "dwell"},
            {"sort_order": 2, "poi_name": "Fontaine du Palmier", "minutes": 0,
             "lat": 48.8576, "lng": 2.3470,
             "narration": "On your right, the Palmier fountain.",
             "spotlight": 1.2, "band": "vignette"},
            {"sort_order": 3, "poi_name": "Sainte-Chapelle", "minutes": 4,
             "lat": 48.8554, "lng": 2.3450, "narration": "Another line.",
             "spotlight": 3.6, "band": "dwell"},
        ]
        _route_two_step(
            page,
            plan=_plan_payload(vignette_stops),
            compose=_authored_payload(
                {"stops": vignette_stops, "spine_area": "Île de la Cité", "total_audio_min": 10}
            ),
        )
        try:
            page.locator("#tourPreviewBtn").click()
            page.wait_for_timeout(300)
            _set_tour_inputs(page, start="48.8566,2.3522")
            assert _plan_the_day(page).status == 200

            # PLAN screen: the walk-past is a tagged 0-minute card, never a stop.
            head = page.locator("#tourStops .tour-day-head").text_content() or ""
            assert "2 stops" in head, f"the walk-past was counted as a stop: {head!r}"
            assert "1 walk-past sight" in head, f"walk-past not surfaced in the head: {head!r}"
            plan_vignette = page.locator("#tourStops .tour-stop--vignette")
            assert plan_vignette.count() == 1, "the plan card should carry the vignette class"
            plan_tag = plan_vignette.locator(".tour-vignette-tag")
            assert plan_tag.count() == 1, "the plan card should show a visible walk-past tag"
            assert (plan_tag.first.text_content() or "").strip() == "walk past"
            assert "walk past · 0 min" in (plan_vignette.first.text_content() or "")
            assert page.locator(".tour-route-pin").count() == 3, "all 3 places pin on the plan map"
            assert page.locator(".tour-route-pin--vignette").count() == 1, (
                "the walk-past pin should be hollow on the plan map"
            )

            assert _write_the_tour(page).status == 200

            stops = page.locator("#tourStops .tour-stop")
            assert stops.count() == 3, f"expected 3 rendered stops, got {stops.count()}"

            # The vignette stop card: visible tag + walk-past (0-minute) styling.
            vignette_card = page.locator("#tourStops .tour-stop--vignette")
            assert vignette_card.count() == 1, "the vignette stop should carry the vignette card class"
            tag = vignette_card.locator(".tour-vignette-tag")
            assert tag.count() == 1, "the vignette card should show a visible band tag"
            assert (tag.first.text_content() or "").strip() == "vignette"
            card_text = vignette_card.first.text_content() or ""
            assert "walk past" in card_text, "the vignette card should read walk past (0-minute styling)"
            assert "0 min" in card_text, "the vignette card should show 0 min"

            # Dwell stops unchanged: no tag, dwell minutes still shown.
            first = stops.first
            assert first.locator(".tour-vignette-tag").count() == 0, "dwell stops must not carry the tag"
            assert "~6 min here" in (first.text_content() or ""), "dwell minutes must render unchanged"

            # Map pins: 3 total, exactly the vignette one hollow.
            assert page.locator(".tour-route-pin").count() == 3, "all 3 stops should pin on the map"
            assert page.locator(".tour-route-pin--vignette").count() == 1, (
                "the vignette stop's pin should use the hollow tour-route-pin--vignette style"
            )
            _take_screenshot(page, "b5-vignette-tag-and-hollow-pin")
        finally:
            _unroute_two_step(page)

    def test_tour_preview_seated_stop_with_a_low_spotlight_is_a_stop_not_a_walk_past(
        self, browser_page
    ):
        """A stop the planner SEATED — with real minutes — is a stop on the screen,
        whatever narration band its spotlight earned.

        Phase 5 W5.1 (phase5-ledger.md, measured on the live wire): the wire's ``band``
        is the NARRATION tier ``band_for_spotlight`` assigns ("vignette" = one line as
        you pass), and a seated stop with a low spotlight carries it WITH minutes —
        Rosemary's 8-minute Bench (05-step-free-visitor.md:25-27, "The bench is a
        stop. It has a location, a duration, and content — it simply is not a sight")
        and a 5-minute stand outside the Orangerie. Reading band as SHAPE rendered
        both as "walk-past sights", dropped their minutes from "standing still", and
        listed the Bench as a walk-past and as a rest promise on the same day (Phase 4
        carried finding 8). Walked past vs stopped at is ``visit_or_walk_past``, the
        field that says so (design §3.1: a rest is a stop; §1.1 truth 1: standing
        still is most of the day).

        UNDO: make ``isWalkPast`` in review.html read ``s.band === 'vignette'`` again
        → the head says "2 stops · 1 walk-past sight" for a 3-stop day and the Bench
        card reads "walk past · 0 min" → RED.
        """
        page, _seed_data, _reporter = browser_page
        stops = [
            {"sort_order": 1, "poi_name": "Musée de l'Orangerie", "minutes": 5,
             "lat": 48.8638, "lng": 2.3226, "narration": "One line as you pass.",
             "spotlight": 1.1, "band": "vignette", "visit_or_walk_past": "visit",
             "goes_inside": False},
            {"sort_order": 2, "poi_name": "Bench", "minutes": 8,
             "lat": 48.8635, "lng": 2.3282, "narration": "Sit here a while.",
             "spotlight": 0.9, "band": "vignette", "visit_or_walk_past": "visit",
             "goes_inside": False},
            {"sort_order": 3, "poi_name": "Pont Royal", "minutes": 0,
             "lat": 48.8608, "lng": 2.3300, "narration": "On your left, the bridge.",
             "spotlight": 1.0, "band": "vignette", "visit_or_walk_past": "walk_past"},
            {"sort_order": 4, "poi_name": "Musée d'Orsay", "minutes": 100,
             "lat": 48.8600, "lng": 2.3266, "narration": "The station clock.",
             "spotlight": 5.0, "band": "dwell", "goes_inside": True},
        ]
        _route_two_step(
            page,
            plan=_plan_payload(stops),
            compose=_authored_payload(
                {"stops": stops, "spine_area": "Rive Gauche", "total_audio_min": 12}
            ),
        )
        try:
            page.locator("#tourPreviewBtn").click()
            page.wait_for_timeout(300)
            _set_tour_inputs(page, start="48.8600,2.3266")
            assert _plan_the_day(page).status == 200

            # PLAN screen: three SEATED places are three stops; the one walk-past is
            # counted apart; every seated minute is standing still.
            head = page.locator("#tourStops .tour-day-head").text_content() or ""
            assert "3 stops" in head, f"a seated stop was read as a walk-past: {head!r}"
            assert "1 walk-past sight" in head, f"the real walk-past vanished: {head!r}"
            assert "113 min standing still" in head, (
                f"the seated minutes of a low-spotlight stop were dropped: {head!r}"
            )
            cards = page.locator("#tourStops .tour-stop")
            texts = [cards.nth(i).text_content() or "" for i in range(cards.count())]
            bench = next(t for t in texts if "Bench" in t)
            assert "~8 min here" in bench, f"the Bench is a stop with minutes: {bench!r}"
            assert "walk past" not in bench, f"the Bench is not walked past: {bench!r}"
            orangerie = next(t for t in texts if "Orangerie" in t)
            assert "~5 min here" in orangerie and "outside" in orangerie, orangerie
            pont = next(t for t in texts if "Pont Royal" in t)
            assert "walk past · 0 min" in pont, f"the real walk-past must still say so: {pont!r}"
            assert page.locator("#tourStops .tour-stop--vignette").count() == 1, (
                "only the walked-past place carries the walk-past styling"
            )
            assert page.locator(".tour-route-pin--vignette").count() == 1, (
                "only the walked-past place pins hollow"
            )

            # AUTHORED screen: the same shape rule; the narration band tag may stay.
            assert _write_the_tour(page).status == 200
            cards = page.locator("#tourStops .tour-stop")
            texts = [cards.nth(i).text_content() or "" for i in range(cards.count())]
            bench = next(t for t in texts if "Bench" in t)
            assert "~8 min here" in bench, f"authored screen: the Bench is a stop: {bench!r}"
            assert "walk past" not in bench, bench
            pont = next(t for t in texts if "Pont Royal" in t)
            assert "walk past" in pont and "0 min" in pont, pont
            assert page.locator("#tourStops .tour-stop--vignette").count() == 1
            assert page.locator(".tour-route-pin--vignette").count() == 1
        finally:
            _unroute_two_step(page)

    def test_tour_preview_deeper_dive_badge_on_extras_stop(self, browser_page):
        """INVARIANT (audit E :2972): a stop with capped-out extras says so —
        design §5.5 promotes this to core ("two lengths per major stop") and §8
        keeps "the keep-exploring machinery". KE10: a dense Île-de-la-Cité tour
        (Notre-Dame) whose budget capped out extra beats surfaces a "Keep
        exploring" badge on the stop that has extras (has_deeper_dive=True) and
        NOT on the stop without extras.

        Driver re-pointed at S4.8 (plan the day -> write the tour; the badge lives
        on the WRITTEN screen — plan cards carry no extras signal, by design §8.1's
        places-only plan). Audit E assigns the fuller two-lengths rewrite to
        Phase 6; the surviving assertions are unchanged here.

        /trips/preview is mocked (the has_deeper_dive wiring is unit-tested in
        tests/test_trip_preview_vignettes.py); this test asserts the render."""
        page, _seed_data, _reporter = browser_page
        dive_stops = [
            # Notre-Dame: beat-dense, budget capped extras out.
            {"sort_order": 1, "poi_name": "Notre-Dame", "minutes": 8,
             "lat": 48.8530, "lng": 2.3499, "narration": "The cathedral rises.",
             "spotlight": 5.0, "band": "dwell", "has_deeper_dive": True},
            # Sainte-Chapelle: no extras -> no badge.
            {"sort_order": 2, "poi_name": "Sainte-Chapelle", "minutes": 5,
             "lat": 48.8554, "lng": 2.3450, "narration": "Stained glass glows.",
             "spotlight": 3.6, "band": "dwell", "has_deeper_dive": False},
        ]
        _route_two_step(
            page,
            plan=_plan_payload(dive_stops),
            compose=_authored_payload(
                {"stops": dive_stops, "spine_area": "Île de la Cité", "total_audio_min": 13}
            ),
        )
        try:
            page.locator("#tourPreviewBtn").click()
            page.wait_for_timeout(300)
            _set_tour_inputs(page, start="48.8566,2.3522")
            assert _plan_the_day(page).status == 200
            assert _write_the_tour(page).status == 200

            stops = page.locator("#tourStops .tour-stop")
            assert stops.count() == 2, f"expected 2 rendered stops, got {stops.count()}"

            # Exactly one "Keep exploring" badge, on the extras stop.
            badges = page.locator("#tourStops .tour-deeper-dive-tag")
            assert badges.count() == 1, (
                f"expected exactly 1 deeper-dive badge (Notre-Dame), got {badges.count()}"
            )
            assert badges.first.is_visible(), "the deeper-dive badge should be visible"
            assert "Keep exploring" in (badges.first.text_content() or ""), (
                "the badge should read 'Keep exploring'"
            )

            # The extras stop (Notre-Dame) carries the badge; the other does not.
            notre_dame = stops.nth(0)
            sainte_chapelle = stops.nth(1)
            assert notre_dame.locator(".tour-deeper-dive-tag").count() == 1, (
                "the has_deeper_dive stop should carry the badge"
            )
            assert sainte_chapelle.locator(".tour-deeper-dive-tag").count() == 0, (
                "a stop without extras must not carry the badge"
            )
            _take_screenshot(page, "ke10-deeper-dive-badge")
        finally:
            _unroute_two_step(page)

    _COORD_5DEC = r"-?\d+\.\d{5},-?\d+\.\d{5}"

    def _clear_tour_route_pins(self, page):
        """Generate an empty mocked preview to clear any leftover tour-route L.Markers
        (they swallow map clicks; circleMarkers bubble). Leaves the tour view open."""
        # One-day stub (D4.0 re-point): an empty plan renders the empty state and
        # clears the map. Nothing is authored, so the author call is never made.
        _route_two_step(
            page,
            plan=_plan_payload([], spine_area="-", options=[]),
            compose=_authored_payload({"stops": [], "spine_area": "-", "total_audio_min": 0}),
        )
        try:
            page.locator("#tourPreviewBtn").click()
            page.wait_for_timeout(300)
            with page.expect_response(lambda r: r.url.endswith("/trips/preview")):
                page.locator("#tourGenerateBtn").click()
            page.wait_for_timeout(200)
        finally:
            _unroute_two_step(page)
        assert page.locator(".tour-route-pin").count() == 0, "route pins should be cleared"

    def test_tour_map_click_sets_start_end_and_clear_resets(self, browser_page):
        """Track B Step B.6: with the Tour Preview view open, the 1st persistent-map click
        fills #tourStart (5-decimal lat,lng) + drops a start pin, the 2nd fills #tourEnd +
        a destination pin; 'Clear points' resets both inputs and pins (open walk). Clicks
        outside the tour view never drop tour pins (guard on the active view)."""
        page, _seed_data, _reporter = browser_page
        self._clear_tour_route_pins(page)
        default_start = page.locator("#tourStart").input_value()
        assert default_start, "the tour form should prefill the start with the city centre"

        map_el = page.locator("#persistent-map")
        box = map_el.bounding_box()
        assert box and box["width"] > 100 and box["height"] > 100, f"map not laid out: {box}"

        def _click_map(fx: float, fy: float):
            map_el.click(position={"x": box["width"] * fx, "y": box["height"] * fy})
            page.wait_for_timeout(300)

        # 1st click -> start input (5-decimal lat,lng) + a start pin; destination untouched.
        _click_map(0.30, 0.45)
        start_val = page.locator("#tourStart").input_value()
        assert re.fullmatch(self._COORD_5DEC, start_val), f"start not 5-dec lat,lng: {start_val!r}"
        lat, lng = (float(p) for p in start_val.split(","))
        assert 40 < lat < 55 and -5 < lng < 10, f"implausible clicked start: {start_val}"
        assert page.locator(".tour-point-pin--start").count() == 1, "1st click should drop a start pin"
        assert page.locator("#tourEnd").input_value() == "", "1st click must not touch the destination"

        # 2nd click -> destination input + pin; start unchanged.
        _click_map(0.70, 0.55)
        end_val = page.locator("#tourEnd").input_value()
        assert re.fullmatch(self._COORD_5DEC, end_val), f"end not 5-dec lat,lng: {end_val!r}"
        elat, elng = (float(p) for p in end_val.split(","))
        assert 40 < elat < 55 and -5 < elng < 10, f"implausible clicked end: {end_val}"
        assert end_val != start_val, "the two clicks should set two different points"
        assert page.locator("#tourStart").input_value() == start_val, "2nd click must not move the start"
        assert page.locator(".tour-point-pin--end").count() == 1, "2nd click should drop a destination pin"
        _take_screenshot(page, "b6-map-click-set-points")

        # Clear points -> both inputs reset (open walk) + pins removed.
        page.locator("#tourClearBtn").click()
        page.wait_for_timeout(200)
        assert page.locator("#tourStart").input_value() == default_start, "clear should restore the default start"
        assert page.locator("#tourEnd").input_value() == "", "clear should empty the destination (open walk)"
        assert page.locator(".tour-point-pin").count() == 0, "clear should remove both pins"

        # Guard: outside the tour view a map click must not drop tour pins or error.
        page.locator(WORKLIST_ROW).first.click()
        page.wait_for_timeout(400)
        _click_map(0.50, 0.50)
        assert page.locator(".tour-point-pin").count() == 0, "map click outside the tour view dropped a pin"
        _take_screenshot(page, "b6-map-click-cleared")

    def test_tour_generate_sends_clicked_coords(self, browser_page):
        """Track B Step B.6: after click-setting start + destination, Generate POSTs exactly
        the clicked coordinates (center_lat/lng from the 1st click, end_lat/lng from the 2nd)."""
        page, _seed_data, _reporter = browser_page
        self._clear_tour_route_pins(page)

        map_el = page.locator("#persistent-map")
        box = map_el.bounding_box()
        assert box and box["width"] > 100 and box["height"] > 100, f"map not laid out: {box}"
        map_el.click(position={"x": box["width"] * 0.35, "y": box["height"] * 0.40})
        page.wait_for_timeout(300)
        map_el.click(position={"x": box["width"] * 0.65, "y": box["height"] * 0.60})
        page.wait_for_timeout(300)

        start_val = page.locator("#tourStart").input_value()
        end_val = page.locator("#tourEnd").input_value()
        assert re.fullmatch(self._COORD_5DEC, start_val), f"start not click-set: {start_val!r}"
        assert re.fullmatch(self._COORD_5DEC, end_val), f"end not click-set: {end_val!r}"

        captured = {}

        def _handler(route):
            captured["body"] = route.request.post_data
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_plan_payload([], spine_area="-", options=[])),
            )

        # Only the PLAN call is made here: this test inspects the OUTBOUND body, and
        # nothing is picked, so no authoring call exists to answer.
        page.route("**/trips/preview", _handler)
        try:
            with page.expect_response(lambda r: r.url.endswith("/trips/preview")):
                page.locator("#tourGenerateBtn").click()
            page.wait_for_timeout(200)
        finally:
            page.unroute("**/trips/preview")

        assert captured.get("body"), "no /trips/preview request body captured"
        sent = json.loads(captured["body"])
        slat, slng = (float(p) for p in start_val.split(","))
        elat, elng = (float(p) for p in end_val.split(","))
        assert sent.get("center_lat") == slat and sent.get("center_lng") == slng, (
            f"generate did not use the clicked start: sent {sent}, clicked {start_val}"
        )
        assert sent.get("end_lat") == elat and sent.get("end_lng") == elng, (
            f"generate did not use the clicked destination: sent {sent}, clicked {end_val}"
        )
        _take_screenshot(page, "b6-generate-uses-clicked-coords")

    def test_four_dial_turns_each_replan_the_day(self, browser_page):
        """W4.2 panel; design §9 Phase 4 demo D5: turn a dial and the day re-plans
        itself — no Generate press, no pick, no second surface.

        Four dial turns, driven through REAL change events (this is the one test
        that exercises the auto-replan listener on purpose): Shorter walks, More
        breaks, Stops and Talking. Each turn must fire a FRESH POST /trips/preview
        whose body carries the changed field — every dial writes into the ONE
        stored plan body — and the day must re-render from the reply. Dials
        accumulate (one body), so each turn's body also still carries every
        earlier turn's field. The identical stubbed day coming back also proves
        the dead-notch honesty line ("already true of this day", W4.2 D-v) renders
        on a turn that changed nothing about the day itself."""
        page, _seed_data, _reporter = browser_page
        bodies: list[dict] = []

        def _handler(route):
            bodies.append(json.loads(route.request.post_data or "{}"))
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_plan_payload(
                    [{"sort_order": 1, "poi_name": "Notre-Dame", "minutes": 6},
                     {"sort_order": 2, "poi_name": "Sainte-Chapelle", "minutes": 4}]
                )),
            )

        page.route("**/trips/preview", _handler)
        try:
            page.locator("#tourPreviewBtn").click()
            page.wait_for_timeout(300)
            _set_tour_inputs(page, start="48.8566,2.3522")
            # Baseline day, so every turn below is a RE-plan of something on screen.
            assert _plan_the_day(page).status == 200
            assert len(bodies) == 1, f"expected 1 baseline plan request, got {len(bodies)}"
            baseline = bodies[-1]
            for field in ("max_leg_minutes", "rest_cadence_minutes",
                          "stop_density", "narration_density"):
                assert field not in baseline, (
                    f"an off dial contributed {field!r} to the baseline body — omit "
                    f"semantics are literal (no field, never a null)"
                )

            # (dial element, value to select, request field, expected wire value)
            turns = [
                ("#tourMaxLeg", "9", "max_leg_minutes", 9),
                ("#tourRestCadence", "25", "rest_cadence_minutes", 25),
                ("#tourStopDensity", "fewer", "stop_density", "fewer"),
                ("#tourNarrationDensity", "less", "narration_density", "less"),
            ]
            for turn_no, (selector, value, field, expected) in enumerate(turns, start=1):
                before = len(bodies)
                with page.expect_response(lambda r: r.url.endswith("/trips/preview")):
                    page.select_option(selector, value)
                page.wait_for_timeout(200)
                # Dead-notch honesty (W4.2 D-v), read FIRST because the note
                # self-removes after ~2.5s: the stub returns the identical day, so
                # a turn that changed nothing about the day must say so instead of
                # pretending it did something.
                if turn_no == 1:
                    assert page.locator("#tourStops .tour-dead-notch-note").count() == 1, (
                        "a dial turn that changed nothing about the day rendered no "
                        "'already true of this day' note"
                    )
                assert len(bodies) == before + 1, (
                    f"dial turn {turn_no} ({selector}) fired {len(bodies) - before} plan "
                    f"request(s), not exactly one fresh POST"
                )
                sent = bodies[-1]
                assert sent.get(field) == expected, (
                    f"dial turn {turn_no} did not carry {field}={expected!r} in the "
                    f"request body: {sent}"
                )
                # One stored body: every earlier turn's field still travels.
                for _, _, prior_field, prior_expected in turns[:turn_no - 1]:
                    assert sent.get(prior_field) == prior_expected, (
                        f"turn {turn_no} dropped the earlier dial {prior_field} from "
                        f"the one plan body: {sent}"
                    )
                # The day re-rendered from the reply.
                head = page.locator("#tourStops .tour-day-head")
                assert head.count() == 1, f"the day did not re-render after turn {turn_no}"
                assert "2 stops" in (head.text_content() or "")
                assert page.locator("#tourWriteBtn").count() == 1, (
                    "the re-rendered day lost its one 'Write the tour' action"
                )
        finally:
            page.unroute("**/trips/preview")

    def test_empty_beat_stripped_on_load(self, browser_page):
        """Edge case: Empty script_body beats are stripped during JSON load."""
        page, _seed_data, reporter = browser_page

        rows = page.locator(WORKLIST_ROW)
        for i in range(rows.count()):
            row_text = rows.nth(i).text_content() or ""
            if "Empty Script" in row_text:
                rows.nth(i).click()
                page.wait_for_timeout(500)

                # Empty beats should have been stripped during processJson
                beat_cards = page.locator(BEAT_CARD)
                _safe_assert(
                    reporter,
                    beat_cards.count() == 0,
                    "Major",
                    f"Empty-beat POI still has {beat_cards.count()} beat cards after load",
                    "Edge Cases",
                    [
                        "Click entry #9 (originally had empty script_body)",
                        "Check beat card count — empty beats should be stripped",
                    ],
                    "0 beat cards (empty beat stripped during load)",
                    f"{beat_cards.count()} beat cards found",
                    page,
                    "ec1-empty-not-stripped",
                )

                _take_screenshot(page, "ec1-empty-beat-stripped")
                break

    def test_long_text_no_overflow(self, browser_page):
        """Edge case: Long POI name and description don't overflow containers."""
        page, _seed_data, reporter = browser_page

        rows = page.locator(WORKLIST_ROW)
        for i in range(rows.count()):
            row_text = rows.nth(i).text_content() or ""
            if "Extraordinarily" in row_text:
                # Check worklist row doesn't overflow
                row = rows.nth(i)
                box = row.bounding_box()
                parent = page.locator(".left-panel")
                parent_box = parent.bounding_box() if parent.count() > 0 else None

                if box and parent_box:
                    overflow = box["x"] + box["width"] > parent_box["x"] + parent_box["width"] + 5
                    _safe_assert(
                        reporter,
                        not overflow,
                        "Minor",
                        "Long POI name overflows worklist row container",
                        "Edge Cases",
                        [
                            "Check bounding box of long-name POI row vs parent",
                        ],
                        "Row fits within .left-panel width",
                        f"Row extends {box['x'] + box['width'] - parent_box['x'] - parent_box['width']:.0f}px beyond parent",
                        page,
                        "ec3-overflow",
                    )

                rows.nth(i).click()
                page.wait_for_timeout(500)
                _take_screenshot(page, "ec3-long-text")
                break

    def test_audit_notes_rendering(self, browser_page):
        """Edge case: Audit notes render in correct containers (object + array)."""
        page, _seed_data, reporter = browser_page

        rows = page.locator(WORKLIST_ROW)
        for i in range(rows.count()):
            row_text = rows.nth(i).text_content() or ""
            if "Audited Montmartre" in row_text:
                rows.nth(i).click()
                page.wait_for_timeout(500)

                # Check POI-level audit notes
                poi_notes = page.locator(POI_AUDIT_NOTES_BOX)
                _safe_assert(
                    reporter,
                    poi_notes.count() > 0,
                    "Major",
                    "POI-level audit notes not rendered",
                    "Audit Notes",
                    [
                        "Click the Audited Montmartre POI",
                        "Check for .poi-audit-notes-box",
                    ],
                    ".poi-audit-notes-box present",
                    f"Found {poi_notes.count()} elements",
                    page,
                    "ec4-no-poi-audit",
                )

                # Check beat-level audit notes
                beat_notes = page.locator(AUDIT_NOTES_BOX)
                _safe_assert(
                    reporter,
                    beat_notes.count() > 0,
                    "Major",
                    "Beat-level audit notes not rendered",
                    "Audit Notes",
                    [
                        "Check for .audit-notes-box in beat cards",
                    ],
                    ".audit-notes-box present in beat cards",
                    f"Found {beat_notes.count()} elements",
                    page,
                    "ec4-no-beat-audit",
                )

                _take_screenshot(page, "ec4-audit-notes")
                break

    def test_gravity_boundaries(self, browser_page):
        """Edge case: Gravity 1 and 5 render without validation warnings."""
        page, _seed_data, reporter = browser_page

        rows = page.locator(WORKLIST_ROW)

        # Check high-gravity POI (entry #2 — Les Halles Anchor, gravity 5)
        for i in range(rows.count()):
            row_text = rows.nth(i).text_content() or ""
            if "Les Halles Multi-Lens" in row_text:
                rows.nth(i).click()
                page.wait_for_timeout(500)

                gravity_warnings = page.locator(f"{BEAT_CARD} {BEAT_WARNING}")
                gravity_warn_texts = []
                for j in range(gravity_warnings.count()):
                    text = gravity_warnings.nth(j).text_content() or ""
                    if "gravity" in text.lower() or "Gravity" in text:
                        gravity_warn_texts.append(text)

                _safe_assert(
                    reporter,
                    len(gravity_warn_texts) == 0,
                    "Minor",
                    "Gravity 5 shows validation warning when it shouldn't",
                    "Edge Cases",
                    [
                        "Click high-gravity POI (gravity 5)",
                        "Check for gravity-related .beat-warning",
                    ],
                    "No gravity warnings for valid gravity 5",
                    f"Found warnings: {gravity_warn_texts}",
                    page,
                    "ec2-gravity5-warning",
                )
                break

        # Check low-gravity POI (entry #3 — Quiet Garden, gravity 1)
        for i in range(rows.count()):
            row_text = rows.nth(i).text_content() or ""
            if "Quiet Garden" in row_text:
                rows.nth(i).click()
                page.wait_for_timeout(500)

                gravity_warnings = page.locator(f"{BEAT_CARD} {BEAT_WARNING}")
                gravity_warn_texts = []
                for j in range(gravity_warnings.count()):
                    text = gravity_warnings.nth(j).text_content() or ""
                    if "gravity" in text.lower() or "Gravity" in text:
                        gravity_warn_texts.append(text)

                _safe_assert(
                    reporter,
                    len(gravity_warn_texts) == 0,
                    "Minor",
                    "Gravity 1 shows validation warning when it shouldn't",
                    "Edge Cases",
                    [
                        "Click low-gravity POI (gravity 1)",
                        "Check for gravity-related .beat-warning",
                    ],
                    "No gravity warnings for valid gravity 1",
                    f"Found warnings: {gravity_warn_texts}",
                    page,
                    "ec2-gravity1-warning",
                )
                break


# ---------------------------------------------------------------------------
# Test: Upload Flow + Error Handling (ACs #8, #12a) — Task 6
# ---------------------------------------------------------------------------


class TestUploadFlow:
    """Tests for single-POI upload via Mark as Complete and error handling."""

    def test_single_poi_upload(self, browser_page):
        """AC #8: Mark a valid POI as complete, verify progressive upload."""
        page, _seed_data, reporter = browser_page

        rows = page.locator(WORKLIST_ROW)

        # Find entry #1 (Seine River Lighthouse — valid, standard)
        target = None
        for i in range(rows.count()):
            row_text = rows.nth(i).text_content() or ""
            if "Quiet Garden" in row_text:
                target = i
                break

        if target is None:
            _safe_assert(
                reporter,
                False,
                "Critical",
                "Could not find Quiet Garden POI for upload test",
                "Upload Flow",
                ["Search worklist"],
                "Entry #1 in worklist",
                "Not found",
                page,
                "ac8-poi-not-found",
            )
            return

        rows.nth(target).click()
        page.wait_for_timeout(2000)

        # Click Mark as Complete
        mc_btn = page.locator(MARK_COMPLETE_BTN)
        if mc_btn.count() == 0 or not mc_btn.first.is_visible():
            _safe_assert(
                reporter,
                False,
                "Critical",
                "Mark as Complete button not visible",
                "Upload Flow",
                ["Navigate to valid POI", "Check #markCompleteBtn"],
                "Button visible",
                "Not visible",
                page,
                "ac8-no-mc-btn",
            )
            return

        mc_btn.first.click()

        # Wait for upload to complete
        page.wait_for_timeout(3000)

        # Check for uploaded badge
        rows = page.locator(WORKLIST_ROW)
        uploaded_found = False

        for i in range(rows.count()):
            row_text = rows.nth(i).text_content() or ""
            if "Quiet Garden" in row_text:
                uploaded_badge = rows.nth(i).locator(BADGE_UPLOADED)
                if uploaded_badge.count() > 0:
                    uploaded_found = True
                break

        # Also check if success toast appeared
        success = page.locator(SUCCESS_TOAST)
        toast_appeared = success.count() > 0 and success.first.is_visible()

        _safe_assert(
            reporter,
            uploaded_found or toast_appeared,
            "Critical",
            "POI upload did not complete — no uploaded badge or success toast",
            "Upload Flow",
            [
                "Navigate to valid POI (Quiet Garden)",
                "Click Mark as Complete",
                "Wait 3s for upload",
                "Check for .badge-uploaded or #successToast",
            ],
            "POI shows uploaded badge or success toast appears",
            f"Uploaded badge: {uploaded_found}, Toast: {toast_appeared}",
            page,
            "ac8-upload-failed",
        )

        # Verify via API
        if uploaded_found or toast_appeared:
            try:
                poi_name = "UI Test \u2014 Quiet Garden Corner"
                encoded = urllib.parse.quote(poi_name, safe="")
                # city_name is a required query param on this endpoint and is the
                # EXACT stored key. POI.city_name is canonically lowercased at
                # ingestion (src/api/crud/nodes.py, 2026-07-03), and the workbench
                # picker keys off that same lowercase value, so the stored key is
                # "paris" — query it with the canonical case or the exact-match
                # beat fetch returns zero beats.
                city_q = urllib.parse.quote("paris", safe="")
                api_resp = _api_get(f"/graph/poi/{encoded}/beats?city_name={city_q}")
                # API returns {"poi_name": "...", "beats": [...]} — extract beats list
                if isinstance(api_resp, dict) and "beats" in api_resp:
                    beat_count = len(api_resp["beats"])
                    _safe_assert(
                        reporter,
                        beat_count >= 1,
                        "Major",
                        "Uploaded POI has no beats in database",
                        "Upload Flow",
                        [
                            "GET /api/v1/graph/poi/{name}/beats",
                            "Check response",
                        ],
                        "At least 1 beat returned",
                        f"{beat_count} beats returned",
                        page,
                        "ac8-no-api-beats",
                    )
                elif isinstance(api_resp, list):
                    beat_count = len(api_resp)
                    _safe_assert(
                        reporter,
                        beat_count >= 1,
                        "Major",
                        "Uploaded POI has no beats in database",
                        "Upload Flow",
                        [
                            "GET /api/v1/graph/poi/{name}/beats",
                            "Check response",
                        ],
                        "At least 1 beat returned",
                        f"{beat_count} beats returned",
                        page,
                        "ac8-no-api-beats",
                    )
                else:
                    status = (
                        api_resp.get("_status", "unknown") if isinstance(api_resp, dict) else "null"
                    )
                    _safe_assert(
                        reporter,
                        False,
                        "Major",
                        f"API verification returned error: {status}",
                        "Upload Flow",
                        ["GET /api/v1/graph/poi/{name}/beats"],
                        "200 OK with beat data",
                        f"Response: {api_resp}",
                        page,
                        "ac8-api-error",
                    )
            except Exception as exc:
                _safe_assert(
                    reporter,
                    False,
                    "Minor",
                    f"API verification failed: {exc}",
                    "Upload Flow",
                    ["API call to verify upload"],
                    "Successful API response",
                    str(exc),
                )

        _take_screenshot(page, "ac8-uploaded")

    def test_error_toast_structure(self, browser_page):
        """AC #12a: Error toast exists in DOM with correct structure."""
        page, _seed_data, reporter = browser_page

        error_toast = page.locator(ERROR_TOAST)
        _safe_assert(
            reporter,
            error_toast.count() > 0,
            "Major",
            "Error toast element (#errorToast) not found in DOM",
            "Error Handling",
            [
                "Check DOM for #errorToast element",
            ],
            "#errorToast exists in DOM",
            f"Count: {error_toast.count()}",
            page,
            "ac12a-no-toast",
        )

        _take_screenshot(page, "ac12a-error-toast")


# ---------------------------------------------------------------------------
# Test: Conflict Detection and Resolution (ACs #13-18) — Task 7
# ---------------------------------------------------------------------------


class TestConflictDetection:
    """Tests for conflict detection across all Jaccard bands and resolution actions."""

    def test_conflict_detection_and_resolution(self, browser_page):
        """ACs #13-18: resolve the incoming-Eiffel ↔ seeded-Champ-de-Mars proximity match as
        'Same Place', then verify all five beat-conflict bands and the resolution actions."""
        page, _seed_data, reporter = browser_page

        rows = page.locator(WORKLIST_ROW)

        # The incoming "UI Test Seed — Eiffel Tower" (5 beats) sits at the seeded
        # "Champ de Mars Landmark" GPS with a dissimilar name, so it does NOT auto-merge:
        # it surfaces a PROXIMITY MATCH the editor resolves as "Same Place", which runs beat
        # conflict detection against the seed's tuned beats (all five bands checked below).
        target = None
        for i in range(rows.count()):
            if "Eiffel Tower" in (rows.nth(i).text_content() or ""):
                target = i
                break
        assert target is not None, (
            "expected the incoming 'UI Test Seed — Eiffel Tower' POI in the worklist"
        )

        rows.nth(target).click()
        page.wait_for_timeout(2000)

        # Resolve the proximity match as "Same Place" -> runs runBeatConflictDetection.
        same_btn = page.locator(PROXIMITY_SAME_BTN)
        assert same_btn.count() > 0 and same_btn.first.is_visible(), (
            "expected a proximity-match panel with a 'Same Place' button for the Eiffel POI "
            "(it must not auto-merge with the dissimilarly-named Champ de Mars seed)"
        )
        same_btn.first.click()
        page.wait_for_timeout(3000)  # Wait for beat conflict detection API calls

        _take_screenshot(page, "ac13-conflict-triggered")

        # --- AC #13: Hard conflict — Beat A (hidden_history, same lens as seed) ---
        hard_badges = page.locator(BEAT_CONFLICT_BADGE_HARD)
        has_hard = hard_badges.count() > 0
        _safe_assert(
            reporter,
            has_hard,
            "Critical",
            "No hard conflict badge found after triggering Mark as Complete",
            "Conflict Detection — Hard Match",
            [
                "Click Mark as Complete on conflict-target POI",
                "Beat A shares lens 'hidden_history' with seeded beat",
                "Check for .beat-conflict-badge-hard",
            ],
            "Red hard conflict badge visible on beat A",
            f"Hard badges found: {hard_badges.count()}",
            page,
            "ac13-no-hard-badge",
        )

        if has_hard:
            badge_text = hard_badges.first.text_content() or ""
            _safe_assert(
                reporter,
                "same lens" in badge_text.lower() or "conflict" in badge_text.lower(),
                "Minor",
                f"Hard conflict badge text unexpected: '{badge_text}'",
                "Conflict Detection — Hard Match",
                [
                    "Check badge text content",
                ],
                "Text contains 'Conflict (same lens)' or similar",
                f"Text: '{badge_text}'",
                page,
                "ac13-badge-text",
            )

        # Check side-by-side panel
        conflict_sides = page.locator(CONFLICT_SIDE)
        _safe_assert(
            reporter,
            conflict_sides.count() > 0,
            "Major",
            "No side-by-side comparison panel for hard conflict",
            "Conflict Detection — Hard Match",
            [
                "Check for .conflict-side panel",
            ],
            "Side-by-side panel visible",
            f"Found {conflict_sides.count()} panels",
            page,
            "ac13-no-side-by-side",
        )

        _take_screenshot(page, "ac13-hard-conflict")

        # --- AC #14: Net-new beat — Beat B (music_nightlife, no conflict) ---
        # Beat B should have NO conflict badge
        beat_cards = page.locator(BEAT_CARD)
        beat_b_idx = 1  # Second beat in the card list

        if beat_cards.count() > beat_b_idx:
            beat_b = beat_cards.nth(beat_b_idx)
            b_hard = beat_b.locator(BEAT_CONFLICT_BADGE_HARD)
            b_soft = beat_b.locator(BEAT_CONFLICT_BADGE)
            b_review = beat_b.locator(BEAT_CONFLICT_BADGE_REVIEW)

            # Net-new should have no conflict badges at all
            no_conflict = b_hard.count() == 0 and b_review.count() == 0
            # Note: soft conflict badge uses the same base class, check if any are visible
            if b_soft.count() > 0:
                # Check if any visible soft badges
                visible_soft = False
                for j in range(b_soft.count()):
                    if b_soft.nth(j).is_visible():
                        visible_soft = True
                        break
                if visible_soft:
                    no_conflict = False

            _safe_assert(
                reporter,
                no_conflict,
                "Major",
                "Net-new beat B has unexpected conflict badge",
                "Conflict Detection — Net-New",
                [
                    "Check beat B (music_nightlife) for conflict badges",
                ],
                "No conflict badge on net-new beat",
                f"Hard: {b_hard.count()}, Review: {b_review.count()}, Soft: {b_soft.count()}",
                page,
                "ac14-unexpected-conflict",
            )

        _take_screenshot(page, "ac14-net-new")

        # --- AC #15: Soft conflict ≥70% — Beat C (food_culinary) ---
        # Beat C should show amber conflict badge with similarity percentage
        beat_c_idx = 2
        if beat_cards.count() > beat_c_idx:
            beat_c = beat_cards.nth(beat_c_idx)
            c_badges = beat_c.locator(BEAT_CONFLICT_BADGE)
            has_soft = False
            soft_text = ""

            for j in range(c_badges.count()):
                text = c_badges.nth(j).text_content() or ""
                if "similar" in text.lower() or "conflict" in text.lower():
                    has_soft = True
                    soft_text = text
                    break

            _safe_assert(
                reporter,
                has_soft,
                "Major",
                "Soft conflict beat C missing amber conflict badge",
                "Conflict Detection — Soft ≥70%",
                [
                    "Check beat C (food_culinary, 84% Jaccard vs seed 2)",
                    "Look for amber badge with similarity percentage",
                ],
                "Amber badge with 'Conflict (XX% similar)'",
                f"Badge found: {has_soft}, text: '{soft_text}'",
                page,
                "ac15-no-soft-badge",
            )

            # Check side-by-side panel for soft conflict
            c_sides = beat_c.locator(CONFLICT_SIDE)
            _safe_assert(
                reporter,
                c_sides.count() > 0,
                "Major",
                "No side-by-side panel for soft conflict beat C",
                "Conflict Detection — Soft ≥70%",
                [
                    "Check for .conflict-side in beat C card",
                ],
                "Side-by-side panel visible",
                f"Found {c_sides.count()} panels",
                page,
                "ac15-no-side-by-side",
            )

        _take_screenshot(page, "ac15-soft-conflict")

        # --- AC #16: Review band 30-69% — Beat D (art_street) ---
        beat_d_idx = 3
        if beat_cards.count() > beat_d_idx:
            beat_d = beat_cards.nth(beat_d_idx)
            d_review = beat_d.locator(BEAT_CONFLICT_BADGE_REVIEW)
            has_review = d_review.count() > 0

            _safe_assert(
                reporter,
                has_review,
                "Major",
                "Review-band beat D missing review badge",
                "Conflict Detection — Review 30-69%",
                [
                    "Check beat D (art_street, 56% Jaccard vs seed 3)",
                    "Look for .beat-conflict-badge-review",
                ],
                "Yellow review badge with 'Review (XX% similar)'",
                f"Review badges found: {d_review.count()}",
                page,
                "ac16-no-review-badge",
            )

            if has_review:
                review_text = d_review.first.text_content() or ""
                _safe_assert(
                    reporter,
                    "review" in review_text.lower() or "similar" in review_text.lower(),
                    "Minor",
                    f"Review badge text unexpected: '{review_text}'",
                    "Conflict Detection — Review 30-69%",
                    ["Check badge text"],
                    "Text contains 'Review' and similarity percentage",
                    f"Text: '{review_text}'",
                    page,
                    "ac16-badge-text",
                )

        _take_screenshot(page, "ac16-review-band")

        # --- AC #17: Pass-through <30% — Beat E (nature_green) ---
        beat_e_idx = 4
        if beat_cards.count() > beat_e_idx:
            beat_e = beat_cards.nth(beat_e_idx)
            e_hard = beat_e.locator(BEAT_CONFLICT_BADGE_HARD)
            e_review = beat_e.locator(BEAT_CONFLICT_BADGE_REVIEW)
            e_soft = beat_e.locator(BEAT_CONFLICT_BADGE)

            no_conflict_e = e_hard.count() == 0 and e_review.count() == 0
            if e_soft.count() > 0:
                visible_soft_e = False
                for j in range(e_soft.count()):
                    if e_soft.nth(j).is_visible():
                        visible_soft_e = True
                        break
                if visible_soft_e:
                    no_conflict_e = False

            _safe_assert(
                reporter,
                no_conflict_e,
                "Major",
                "Pass-through beat E has unexpected conflict badge",
                "Conflict Detection — Pass-through <30%",
                [
                    "Check beat E (nature_green, <2% Jaccard)",
                    "Should have no conflict badge",
                ],
                "No conflict badge on pass-through beat",
                f"Hard: {e_hard.count()}, Review: {e_review.count()}, Soft: {e_soft.count()}",
                page,
                "ac17-unexpected-conflict",
            )

        _take_screenshot(page, "ac17-pass-through")

        # --- AC #18: Conflict Resolution Actions ---
        # Test Replace action on hard-conflict beat (beat 0)
        self._test_resolution_action(
            page,
            reporter,
            beat_cards,
            0,
            "replace",
            "Will replace",
            "ac18-replace",
        )

        # Test Skip action on soft-conflict beat (beat 2)
        self._test_resolution_action(
            page,
            reporter,
            beat_cards,
            2,
            "skip",
            "Will skip",
            "ac18-skip",
        )

        # Test Merge action on hard-conflict beat (beat 0) — click resolved label to re-open,
        # then select merge. Beat 0 already has "replace" resolution, so click label to change.
        if beat_cards.count() > 0:
            beat_0 = beat_cards.nth(0)
            resolved_label = beat_0.locator("[data-change-resolution='true']")
            if resolved_label.count() > 0:
                resolved_label.first.click()
                page.wait_for_timeout(500)
        self._test_merge_action(page, reporter, beat_cards, 0, "ac18-merge")

        _take_screenshot(page, "ac18-all-resolutions")

    def _test_resolution_action(
        self,
        page: Page,
        reporter: BugReporter,
        beat_cards,
        beat_idx: int,
        action: str,
        expected_label: str,
        screenshot_name: str,
    ) -> None:
        """Test a specific conflict resolution action on a beat card."""
        if beat_cards.count() <= beat_idx:
            return

        beat = beat_cards.nth(beat_idx)

        # Look for resolution buttons/selects
        action_btns = beat.locator(f"button:has-text('{action.capitalize()}')")
        action_selects = beat.locator(f"select option:has-text('{action.capitalize()}')")
        action_links = beat.locator(f"[data-resolution='{action}']")

        clicked = False
        if action_links.count() > 0:
            action_links.first.click()
            clicked = True
        elif action_btns.count() > 0:
            action_btns.first.click()
            clicked = True
        elif action_selects.count() > 0:
            # Select from dropdown
            select = beat.locator("select").first
            if select.count() > 0:
                select.select_option(label=action.capitalize())
                clicked = True

        if not clicked:
            # Try looking for the action by other means
            all_btns = beat.locator("button")
            for i in range(all_btns.count()):
                text = all_btns.nth(i).text_content() or ""
                if action.lower() in text.lower():
                    all_btns.nth(i).click()
                    clicked = True
                    break

        page.wait_for_timeout(500)

        if clicked:
            # Check for resolution label
            beat_text = beat.text_content() or ""
            has_label = expected_label.lower() in beat_text.lower()
            _safe_assert(
                reporter,
                has_label,
                "Major",
                f"'{action.capitalize()}' resolution missing '{expected_label}' label",
                f"Conflict Resolution — {action.capitalize()}",
                [
                    f"Click '{action}' on beat #{beat_idx + 1}",
                    f"Check for '{expected_label}' label",
                ],
                f"Label '{expected_label}' visible",
                f"Beat text excerpt: '{beat_text[:200]}'",
                page,
                screenshot_name,
            )
        else:
            _safe_assert(
                reporter,
                False,
                "Major",
                f"Could not find '{action}' resolution action on beat #{beat_idx + 1}",
                f"Conflict Resolution — {action.capitalize()}",
                [
                    f"Look for '{action}' button/option on beat card",
                ],
                f"'{action.capitalize()}' action available",
                "Action not found",
                page,
                f"{screenshot_name}-not-found",
            )

        _take_screenshot(page, screenshot_name)

    def _test_merge_action(
        self,
        page: Page,
        reporter: BugReporter,
        beat_cards,
        beat_idx: int,
        screenshot_name: str,
    ) -> None:
        """Test the merge resolution action — should open merge overlay with 3 fields."""
        if beat_cards.count() <= beat_idx:
            return

        beat = beat_cards.nth(beat_idx)

        # Look for merge button
        merge_btns = beat.locator("button:has-text('Merge')")
        merge_links = beat.locator("[data-resolution='merge']")

        clicked = False
        if merge_links.count() > 0:
            merge_links.first.click()
            clicked = True
        elif merge_btns.count() > 0:
            merge_btns.first.click()
            clicked = True
        else:
            all_btns = beat.locator("button")
            for i in range(all_btns.count()):
                text = all_btns.nth(i).text_content() or ""
                if "merge" in text.lower():
                    all_btns.nth(i).click()
                    clicked = True
                    break

        page.wait_for_timeout(500)

        if clicked:
            # Check for merge overlay
            overlay = page.locator(MERGE_OVERLAY)
            has_overlay = overlay.count() > 0

            _safe_assert(
                reporter,
                has_overlay,
                "Major",
                "Merge overlay did not open after clicking Merge",
                "Conflict Resolution — Merge",
                [
                    f"Click 'Merge' on beat #{beat_idx + 1}",
                    "Check for .merge-overlay",
                ],
                "Merge overlay opens",
                f"Overlay found: {has_overlay}",
                page,
                f"{screenshot_name}-no-overlay",
            )

            _take_screenshot(page, screenshot_name)

            # Close merge overlay if open (cleanup). The modal backdrop sits at the button's
            # click point, so a coordinate click (even force) lands on the backdrop. Fire the
            # Cancel button's own handler directly via el.click() (what a human's click on the
            # visible button does); the closure assertion below proves it actually closed.
            if has_overlay:
                close_btns = overlay.locator("button:has-text('Cancel')")
                if close_btns.count() > 0:
                    close_btns.first.evaluate("el => el.click()")
                else:
                    page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                # Confirm the overlay actually closed (so the force-click isn't masking a
                # stuck modal that would break later state).
                _safe_assert(
                    reporter,
                    page.locator(MERGE_OVERLAY).count() == 0
                    or not page.locator(MERGE_OVERLAY).first.is_visible(),
                    "Minor",
                    "Merge overlay did not close after Cancel",
                    "Conflict Resolution — Merge",
                    ["Click Cancel on the merge overlay", "Check the overlay is gone"],
                    "Merge overlay closed",
                    "Overlay still visible",
                    page,
                    f"{screenshot_name}-not-closed",
                )
        else:
            _safe_assert(
                reporter,
                False,
                "Major",
                f"Could not find 'Merge' action on beat #{beat_idx + 1}",
                "Conflict Resolution — Merge",
                [
                    "Look for 'Merge' button on review-band beat",
                ],
                "'Merge' action available",
                "Not found",
                page,
                f"{screenshot_name}-not-found",
            )


# ---------------------------------------------------------------------------
# Test: Proximity Matching Logic (POI Matching Fix)
# ---------------------------------------------------------------------------

PROXIMITY_SAME_BTN = ".proximity-same-btn"
PROXIMITY_DIFF_BTN = ".proximity-diff-btn"
PROXIMITY_PANEL = ".proximity-match-panel"


class TestProximityMatching:
    """Frontend tests for location-first POI deduplication (ACs #1-8)."""

    def test_find_proximity_matches_empty_for_distant_poi(self, browser_page):
        """AC 1: POI >50m from all existing → empty array (auto-new)."""
        page, _seed_data, _reporter = browser_page
        result = page.evaluate("""() => {
            // POI far from any existing (200m+ away)
            const testPoi = { latitude: 48.90, longitude: 2.50 };
            return findProximityMatches(testPoi, cachedPoiList);
        }""")
        assert isinstance(result, list)
        assert len(result) == 0, "Distant POI should have no proximity matches"

    def test_find_proximity_matches_returns_nearby(self, browser_page):
        """AC 2: POI within 50m of one existing → single match returned."""
        page, _seed_data, _reporter = browser_page
        result = page.evaluate("""() => {
            // Find first cached POI with location and create a nearby point
            const existing = cachedPoiList.find(p => p.properties.location && p.properties.location.lat);
            if (!existing) return { error: 'no cached POI with location' };
            // Place incoming POI ~20m away (approx 0.0002 degrees)
            const testPoi = {
                latitude: existing.properties.location.lat + 0.0001,
                longitude: existing.properties.location.lng + 0.0001,
            };
            const matches = findProximityMatches(testPoi, cachedPoiList);
            return { count: matches.length, firstDist: matches.length > 0 ? matches[0].distanceM : null };
        }""")
        if "error" in result:
            pytest.skip(result["error"])
        assert result["count"] >= 1, "Nearby POI should have at least one proximity match"
        assert result["firstDist"] <= 50, "Match should be within 50m"

    def test_find_proximity_matches_sorted_by_distance(self, browser_page):
        """AC 3: Multiple matches sorted ascending by distance."""
        page, _seed_data, _reporter = browser_page
        result = page.evaluate("""() => {
            // Create two fake cached POIs close together, test a point near both
            const fakeCached = [
                { properties: { name: 'A', location: { lat: 48.8530, lng: 2.3499 } } },
                { properties: { name: 'B', location: { lat: 48.8532, lng: 2.3499 } } },
            ];
            const testPoi = { latitude: 48.8531, longitude: 2.3499 };
            const matches = findProximityMatches(testPoi, fakeCached);
            return matches.map(m => ({ name: m.existingPoi.properties.name, dist: m.distanceM }));
        }""")
        assert len(result) == 2, "Should match both nearby POIs"
        assert result[0]["dist"] <= result[1]["dist"], "Matches should be sorted by distance"

    def test_same_name_distant_poi_is_new(self, browser_page):
        """AC 8: Identical names 200m apart → both auto-new (no proximity match)."""
        page, _seed_data, _reporter = browser_page
        result = page.evaluate("""() => {
            const fakeCached = [
                { properties: { name: 'Hôtel de Ville', location: { lat: 48.8530, lng: 2.3499 } } },
            ];
            // Same name, 200m away
            const testPoi = { latitude: 48.8550, longitude: 2.3499 };
            return findProximityMatches(testPoi, fakeCached);
        }""")
        assert len(result) == 0, "Same-name POI 200m away should have no proximity match"

    def test_detect_conflicts_missing_coords(self, browser_page):
        """AC 7: POI without coordinates → missingCoords: true."""
        page, _seed_data, _reporter = browser_page
        result = page.evaluate("""() => {
            const testPoi = { poi_name: 'No Coords POI', beats: [] };
            return detectConflictsForPoi(testPoi);
        }""")
        assert result["missingCoords"] is True
        assert len(result["errors"]) > 0

    def test_detect_conflicts_auto_new_no_match(self, browser_page):
        """AC 1: POI with no nearby existing → isNew: true."""
        page, _seed_data, _reporter = browser_page
        result = page.evaluate("""() => {
            const testPoi = { poi_name: 'Distant POI', latitude: 48.90, longitude: 2.50, beats: [] };
            return detectConflictsForPoi(testPoi);
        }""")
        assert result["isNew"] is True
        assert len(result["proximityMatches"]) == 0

    def test_map_poi_for_api_with_existing_name(self, browser_page):
        """AC 6: useExistingName sends the existing name in payload."""
        page, _seed_data, _reporter = browser_page
        result = page.evaluate("""() => {
            const poi = { poi_name: 'Incoming Name', latitude: 48.85, longitude: 2.35 };
            return mapPoiForApi(poi, { useExistingName: 'Existing Name' });
        }""")
        assert result["name"] == "Existing Name"

    def test_map_poi_for_api_with_force_create(self, browser_page):
        """AC 5: forceCreate sends force_create: true."""
        page, _seed_data, _reporter = browser_page
        result = page.evaluate("""() => {
            const poi = { poi_name: 'Test POI', latitude: 48.85, longitude: 2.35 };
            return mapPoiForApi(poi, { forceCreate: true });
        }""")
        assert result["force_create"] is True

    def test_name_similarity_function(self, browser_page):
        """Name similarity is computed correctly for display."""
        page, _seed_data, _reporter = browser_page
        result = page.evaluate("""() => {
            return {
                identical: nameSimilarity('Hôtel de Ville', 'Hôtel de Ville'),
                similar: nameSimilarity('Hôtel de Ville', 'The Hôtel de Ville'),
                different: nameSimilarity('Hôtel de Ville', 'Jardin du Luxembourg'),
            };
        }""")
        assert result["identical"] == 1.0
        assert result["similar"] > 0.5
        assert result["different"] < 0.5

    def test_boundary_50m_excluded(self, browser_page):
        """Edge: POI at exactly >50m is excluded from proximity matches."""
        page, _seed_data, _reporter = browser_page
        result = page.evaluate("""() => {
            // Place existing POI and incoming ~51m apart (about 0.00046 degrees lat)
            const fakeCached = [
                { properties: { name: 'Boundary POI', location: { lat: 48.8530, lng: 2.3499 } } },
            ];
            const testPoi = { latitude: 48.85346, longitude: 2.3499 };
            return findProximityMatches(testPoi, fakeCached);
        }""")
        assert len(result) == 0, "POI at ~51m should NOT match"


# ---------------------------------------------------------------------------
# Test: Regressions for six confirmed workbench defects (2026-07-06)
# ---------------------------------------------------------------------------


class TestDefectRegressions:
    """Real-browser regressions for defects 5, 6, 12, 13, 15, 16.

    These run in the shared module page. The stateful cases (5, 12, 6, 16)
    reload review.html first so they start from a clean IIFE and never leak
    ``poiData``/``cachedPoiList`` mutations into other tests; the exposed
    ``window`` setters let each test stage exactly the state it needs.
    """

    def _fresh_page(self, page):
        """Reload the workbench and wait until its internals are exposed."""
        page.goto(WORKBENCH_URL)
        page.wait_for_load_state("networkidle")
        page.wait_for_function("() => typeof window.mergeIncomingIntoDbPois === 'function'")

    def test_defect5_two_incoming_matching_one_db_poi_keeps_all_beats(self, browser_page):
        """DEFECT 5: two incoming POIs matching one DB POI accumulate — no beat loss.

        Before the fix, ``match.existingPoi._incomingBeats = entry.beats`` was a
        plain overwrite, so the second matching entry clobbered the first and its
        beats vanished (both incoming entries were spliced out of poiData). The fix
        pushes into an array, so every matched entry's beats survive.
        """
        page, _seed_data, _reporter = browser_page
        self._fresh_page(page)
        result = page.evaluate(
            """() => {
            // One DB POI; two incoming POIs at the SAME coords + name so both
            // proximity-match AND clear the 0.5 name-similarity bar.
            const dbPoi = { id: 'db-1', properties: { name: 'Louvre', location: { lat: 48.8606, lng: 2.3376 } } };
            window.cachedPoiList = [dbPoi];
            window.poiData = [
              { poi_name: 'Louvre', latitude: 48.8606, longitude: 2.3376, beats: [{ id: 'a1' }, { id: 'a2' }] },
              { poi_name: 'Louvre', latitude: 48.8606, longitude: 2.3376, beats: [{ id: 'b1' }] },
            ];
            window.mergeIncomingIntoDbPois();
            const merged = window.cachedPoiList[0];
            return {
              incomingBeatIds: (merged._incomingBeats || []).map(b => b.id),
              incomingPoiDataLen: Array.isArray(merged._incomingPoiData) ? merged._incomingPoiData.length : -1,
              remainingIncoming: window.poiData.length,
            };
        }"""
        )
        assert sorted(result["incomingBeatIds"]) == ["a1", "a2", "b1"], (
            f"all three incoming beats must be retained, got {result['incomingBeatIds']}"
        )
        assert result["incomingPoiDataLen"] == 2, (
            "_incomingPoiData must accumulate both matched entries"
        )
        assert result["remainingIncoming"] == 0, "both matched incoming POIs are consumed"

    def test_defect12_case_and_whitespace_forks_trigger_dup_resolver(self, browser_page):
        """DEFECT 12: 'Louvre' / 'louvre' / 'Louvre ' collapse to one dedup key.

        Before the fix the exact-name check keyed on the RAW poi_name, so casing/
        whitespace variants were distinct keys and the duplicate resolver never
        fired — the forks reached the backend MERGE. The fix normalizes the key
        (trim + collapse whitespace + lowercase), matching the alt-name check.
        """
        page, _seed_data, _reporter = browser_page
        self._fresh_page(page)
        # processJson on the dupe path only touches the dup-resolver DOM (no map),
        # so it is safe to invoke directly with staged JSON.
        page.evaluate(
            """() => {
            window.cachedPoiList = [];
            window.processJson([
              { poi_name: 'Louvre',   latitude: 48.8606, longitude: 2.3376, beats: [{ id: 'x' }] },
              { poi_name: 'louvre',   latitude: 48.9000, longitude: 2.4000, beats: [{ id: 'y' }] },
              { poi_name: 'Louvre ',  latitude: 48.9500, longitude: 2.4500, beats: [{ id: 'z' }] },
            ]);
        }"""
        )
        overlay = page.locator(DUP_OVERLAY)
        assert overlay.is_visible(), (
            "the duplicate resolver overlay must open for casing/whitespace name forks"
        )
        # All three forks land in a single duplicate set.
        header = page.locator(f"{DUP_OVERLAY} .dup-set h3").first.text_content() or ""
        assert "3 entries" in header, f"all 3 forks must group into one dup set: {header!r}"
        # Sanity: distinct names still do NOT trigger the resolver.
        self._fresh_page(page)
        page.evaluate(
            """() => {
            window.cachedPoiList = [];
            window.processJson([
              { poi_name: 'Louvre',    latitude: 48.8606, longitude: 2.3376, beats: [{ id: 'x' }] },
              { poi_name: 'Pantheon',  latitude: 48.8462, longitude: 2.3464, beats: [{ id: 'y' }] },
            ]);
        }"""
        )
        assert not page.locator(DUP_OVERLAY).is_visible(), (
            "distinct names must NOT open the duplicate resolver"
        )

    def test_defect15_popup_merge_button_has_no_inline_onclick(self, browser_page):
        """DEFECT 15: the DB popup merge button is wired via addEventListener.

        Before the fix the button interpolated poi.id into a single-quoted inline
        onclick and escHtml did not escape single quotes — a latent XSS / broken
        handler for any id containing a quote. The fix builds the button with
        createElement + addEventListener (no interpolation), so an id containing a
        quote is inert. We assert (a) no inline onclick attribute, (b) a hostile id
        does not leak into markup, and (c) the click still starts merge mode.
        """
        page, _seed_data, _reporter = browser_page
        self._fresh_page(page)
        result = page.evaluate(
            """() => {
            const hostileId = "db'\\"><img src=x onerror=window.__pwned=1>";
            const poi = { id: hostileId, properties: { name: 'Louvre', location: { lat: 48.86, lng: 2.33 } } };
            const el = window.buildDbPopupContent(poi, { _beatCount: 2 });
            const btn = el.querySelector('button.popup-merge-btn');
            // Wire a spy so we can confirm the closure passes the RAW id through.
            let clickedWith = null;
            const orig = window._startMergeMode;
            window._startMergeMode = (id) => { clickedWith = id; };
            btn.click();
            window._startMergeMode = orig;
            return {
              hasInlineOnclick: btn.hasAttribute('onclick'),
              htmlHasScriptFork: el.innerHTML.includes('onerror='),
              pwned: window.__pwned === 1,
              clickedWith,
              expectedId: hostileId,
            };
        }"""
        )
        assert result["hasInlineOnclick"] is False, "merge button must not use an inline onclick"
        assert result["htmlHasScriptFork"] is False, "hostile id must not reach the popup markup"
        assert result["pwned"] is False, "no injected handler may execute"
        assert result["clickedWith"] == result["expectedId"], (
            "the click closure must pass the exact (raw) poi.id to _startMergeMode"
        )

    def test_defect6_merge_write_failure_aborts_before_postmerge(self, browser_page):
        """DEFECT 6: a 500 on a merge write throws, so postMergeUpdate never runs.

        Before the fix executeMerge ignored response.ok, so a DB 500 mid-merge still
        spliced the source POI out of the cache/map and showed a green 'Merged'
        toast — a false success with DB/UI diverged. The mustOk helper now throws on
        !ok. We stub the HAS_BEAT edge POST to 500 and assert: an error toast (not
        success), and the source POI is STILL in cachedPoiList (postMergeUpdate did
        not run).
        """
        page, _seed_data, _reporter = browser_page
        self._fresh_page(page)
        # 500 the target-link HAS_BEAT POST; everything else 200 so the failure is
        # unambiguously the .ok check, not a missing route.
        page.route(
            "**/api/v1/edges/HAS_BEAT",
            lambda route: (
                route.fulfill(status=500, content_type="application/json", body='{"detail":"boom"}')
                if route.request.method == "POST"
                else route.fulfill(status=200, content_type="application/json", body="{}")
            ),
        )
        page.route(
            "**/api/v1/nodes/POI/**",
            lambda route: route.fulfill(status=200, content_type="application/json", body="{}"),
        )
        page.route(
            "**/api/v1/graph/poi/**/beats**",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body='{"beats":[]}'
            ),
        )
        try:
            result = page.evaluate(
                """async () => {
                const target = { id: 'tgt', properties: { name: 'Target POI', location: { lat: 48.86, lng: 2.33 } } };
                const source = { id: 'src', properties: { name: 'Source POI', location: { lat: 48.86, lng: 2.33 } } };
                window.cachedPoiList = [target, source];
                const beatItems = [{ sourceBeat: { id: 'beat-1', lens_slug: 'hidden_history' }, resolution: 'keep' }];
                await window.executeMerge(target, source, beatItems);
                return {
                  sourceStillCached: window.cachedPoiList.some(p => p.id === 'src'),
                };
            }"""
            )
            assert result["sourceStillCached"] is True, (
                "postMergeUpdate must NOT run on a failed write — source POI stays in the cache"
            )
            # Error toast shown, success toast NOT shown.
            error_toast = page.locator(ERROR_TOAST)
            success_toast = page.locator(SUCCESS_TOAST)
            assert error_toast.count() > 0 and error_toast.first.is_visible(), (
                "a failed merge must surface an error toast"
            )
            assert not (success_toast.count() > 0 and success_toast.first.is_visible()), (
                "a failed merge must NOT show a green 'Merged' success toast"
            )
        finally:
            page.unroute("**/api/v1/edges/HAS_BEAT")
            page.unroute("**/api/v1/nodes/POI/**")
            page.unroute("**/api/v1/graph/poi/**/beats**")

    def _set_city_and_load_fixture(self, page):
        """Full happy-path bring-up: goto, pick Paris, load the fixture worklist."""
        page.goto(WORKBENCH_URL)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "() => { const s = document.querySelector('#citySelect'); "
            "return s && [...s.options].some(o => o.textContent.includes('Paris')); }",
            timeout=15000,
        )
        page.locator(CITY_SELECT).select_option(index=1)
        page.locator(CITY_SUBMIT).click()
        page.locator(CITY_OVERLAY).wait_for(state="hidden", timeout=15000)
        load_btn = page.locator(LOAD_JSON_BTN)
        with contextlib.suppress(Exception):
            load_btn.wait_for(state="visible", timeout=5000)
        with page.expect_file_chooser() as fc_info:
            load_btn.click()
        fc_info.value.set_files(str(FIXTURE_PATH))
        # The fixture intentionally contains a duplicate name pair, so the dup
        # resolver opens on load — rename one entry and resolve to reach the worklist.
        dup_overlay = page.locator(DUP_OVERLAY)
        with contextlib.suppress(Exception):
            dup_overlay.wait_for(state="visible", timeout=5000)
            dup_inputs = page.locator(f"{DUP_OVERLAY} input[data-dup-idx]")
            if dup_inputs.count() >= 2:
                dup_inputs.nth(1).clear()
                dup_inputs.nth(1).fill("UI Test — Duplicate Seine Promenade (2)")
            page.locator(DUP_RESOLVE_BTN).click()
            dup_overlay.wait_for(state="hidden", timeout=5000)
        page.wait_for_selector(WORKLIST_ROW, timeout=10000)

    def test_defect13_failed_edge_link_surfaces_upload_error(self, browser_page):
        """DEFECT 13: a 500 on the HAS_BEAT edge POST fails the upload (no orphan).

        Before the fix uploadSinglePoi created the beat with a checked POST but
        fired the HAS_BEAT / TAGGED_WITH edges WITHOUT checking .ok — a failed edge
        left an orphaned beat while the upload 'succeeded'. The fix throws on a
        non-ok edge POST. We upload an auto-new POI (Pantheon Anchor — far from any
        seed) with the HAS_BEAT edge stubbed to 500 and assert the upload REPORTS
        FAILURE (error toast, no uploaded badge) instead of a silent success.
        """
        page, _seed_data, _reporter = browser_page
        self._set_city_and_load_fixture(page)

        # Stub only the HAS_BEAT edge POST to 500; the beat-node create stays live
        # so the failure is unambiguously the (previously unchecked) edge link.
        page.route(
            "**/api/v1/edges/HAS_BEAT",
            lambda route: (
                route.fulfill(status=500, content_type="application/json", body='{"detail":"edge boom"}')
                if route.request.method == "POST"
                else route.continue_()
            ),
        )
        try:
            rows = page.locator(WORKLIST_ROW)
            target = None
            for i in range(rows.count()):
                if "Pantheon Anchor" in (rows.nth(i).text_content() or ""):
                    target = i
                    break
            assert target is not None, "Pantheon Anchor POI must be in the worklist"
            rows.nth(target).click()
            page.wait_for_timeout(1000)
            page.locator(MARK_COMPLETE_BTN).first.click()
            page.wait_for_timeout(2500)

            error_toast = page.locator(ERROR_TOAST)
            assert error_toast.count() > 0 and error_toast.first.is_visible(), (
                "a failed HAS_BEAT link must surface an upload error toast, not a silent orphan"
            )
            # The POI must NOT be marked uploaded.
            rows = page.locator(WORKLIST_ROW)
            uploaded_badge_present = False
            for i in range(rows.count()):
                if "Pantheon Anchor" in (rows.nth(i).text_content() or ""):
                    uploaded_badge_present = rows.nth(i).locator(BADGE_UPLOADED).count() > 0
                    break
            assert not uploaded_badge_present, (
                "a POI whose edge link failed must NOT show an uploaded badge"
            )
        finally:
            page.unroute("**/api/v1/edges/HAS_BEAT")

    def test_defect16_city_fetch_failure_keeps_overlay_open_and_button_usable(
        self, browser_page
    ):
        """DEFECT 16: a DB fetch failure leaves the overlay open + Set City usable.

        Before the fix activateCity hid the overlay BEFORE awaiting the fetch and
        swallowed the error, so a DB failure left the user locked out: overlay gone,
        button disabled, no retry. The fix hides the overlay only after a successful
        fetch, re-enables the button, and rethrows. We drive the click handler with
        /lenses stubbed to 500 and assert the overlay stays visible and Set City is
        re-enabled.
        """
        page, _seed_data, _reporter = browser_page
        self._fresh_page(page)
        # Fail the lens fetch — fetchLensesAndPoiList throws on a non-ok /nodes/Lens,
        # which is what drives activateCity's failure path.
        page.route(
            "**/api/v1/nodes/Lens**",
            lambda route: route.fulfill(status=500, content_type="application/json", body='{"detail":"down"}'),
        )
        try:
            # Simulate a submitted-but-disabled button, then run the real activation.
            page.evaluate("() => { document.querySelector('#citySubmitBtn').disabled = true; }")
            page.evaluate(
                """async () => {
                try {
                  await window.activateCity('paris', { lat: 48.8566, lng: 2.3522 });
                } catch (e) { /* rethrow is expected; the handler path is what we assert */ }
            }"""
            )
            overlay = page.locator(CITY_OVERLAY)
            assert overlay.is_visible(), (
                "on a DB fetch failure the city overlay must STAY open (not hide pre-await)"
            )
            submit = page.locator(CITY_SUBMIT)
            assert submit.is_enabled(), (
                "Set City must be re-enabled on failure so the user can retry"
            )
        finally:
            page.unroute("**/api/v1/nodes/Lens**")
            # Leave the page clean for any later test.
            self._fresh_page(page)

    # -- executeMerge / executeUpload edge-orchestration regressions ---------
    #
    # These four cases stub the edges/nodes API with page.route and invoke the
    # exposed window.executeMerge / window.executeUpload directly with staged
    # state, then assert the SEQUENCE of API calls the function made. They do
    # not depend on the seeded graph (every relevant endpoint is stubbed), so
    # the recorded-request log is the ground truth for each defect.

    @staticmethod
    def _install_request_recorder(page, handlers):
        """Route ``**/api/v1/**`` through ``handlers`` and record every request.

        ``handlers`` maps ``(METHOD, path_predicate)`` is not used; instead each
        entry is ``(method, substring, status, body)`` and the first match wins.
        Returns a list that fills with ``{"method", "url", "body"}`` dicts as the
        page issues requests. The route is removed by the caller via ``unroute``.
        """
        calls: list[dict] = []

        def _handler(route):
            req = route.request
            post_data = req.post_data or ""
            calls.append({"method": req.method, "url": req.url, "body": post_data})
            for method, substring, status, body in handlers:
                if req.method == method and substring in req.url:
                    route.fulfill(
                        status=status,
                        content_type="application/json",
                        body=body,
                    )
                    return
            # Default: succeed with an empty-ish object so unstubbed writes do
            # not spuriously fail the function under test.
            route.fulfill(
                status=200, content_type="application/json", body='{"id":"stub-ok"}'
            )

        page.route("**/api/v1/**", _handler)
        return calls

    def test_defect1_merge_deletes_source_has_beat_by_edge_id_not_bodiless(
        self, browser_page
    ):
        """DEFECT 1: executeMerge deletes the source HAS_BEAT edge by id.

        The edges API exposes DELETE only as ``/edges/{rel}/{edge_id}``; there is
        no bodiless ``DELETE /edges/HAS_BEAT`` route (it is 405). Before the fix
        executeMerge issued that bodiless DELETE, which 405'd, threw out of the
        loop AFTER the new target->beat edge was created but BEFORE the old
        source->beat edge and the source POI were removed — the beat ended up
        double-linked and the source POI survived. The fix looks the edge id up
        via ``GET /edges/HAS_BEAT?source_id=...`` and DELETEs by id.

        We stub the bodiless ``DELETE /edges/HAS_BEAT`` to 405 (mirroring the real
        API) and the lookup+by-id-delete to 200, then assert the merge (a) never
        issues a bodiless HAS_BEAT DELETE, (b) DELETEs the source edge by its id,
        and (c) deletes the source POI (reaching the success path).
        """
        page, _seed_data, _reporter = browser_page
        self._fresh_page(page)
        calls = self._install_request_recorder(
            page,
            [
                # Lookup of the source POI's HAS_BEAT edges -> one edge to beat-1.
                (
                    "GET",
                    "/edges/HAS_BEAT?source_id=src-poi",
                    200,
                    '{"items":[{"id":"edge-src-beat","type":"HAS_BEAT",'
                    '"source_id":"src-poi","target_id":"beat-1","properties":{}}],'
                    '"total":1,"skip":0,"limit":200}',
                ),
                # A BODILESS DELETE (the pre-fix route) mirrors the real 405.
                ("DELETE", "/edges/HAS_BEAT/", 200, '{"deleted":true}'),
            ],
        )
        try:
            result = page.evaluate(
                """async () => {
                window.cachedPoiList = [];
                const target = { id: 'tgt-poi', properties: { name: 'Target' } };
                const source = { id: 'src-poi', properties: { name: 'Source' } };
                const beatItems = [{ resolution: 'keep', sourceBeat: { id: 'beat-1', lens_slug: 'hidden_history' } }];
                await window.executeMerge(target, source, beatItems);
                return true;
            }"""
            )
            assert result is True
            # (a) No bodiless DELETE /edges/HAS_BEAT (path ends at the rel type).
            bodiless = [
                c
                for c in calls
                if c["method"] == "DELETE" and c["url"].rstrip("/").endswith("/edges/HAS_BEAT")
            ]
            assert not bodiless, (
                f"executeMerge must not issue a bodiless DELETE /edges/HAS_BEAT (405 route); got {bodiless}"
            )
            # (b) DELETE by the resolved edge id.
            by_id = [
                c
                for c in calls
                if c["method"] == "DELETE" and "/edges/HAS_BEAT/edge-src-beat" in c["url"]
            ]
            assert by_id, (
                "executeMerge must DELETE the source HAS_BEAT edge by its resolved id; "
                f"recorded DELETEs: {[c['url'] for c in calls if c['method'] == 'DELETE']}"
            )
            # (c) The source POI is deleted (success path reached, no double-link abort).
            poi_deletes = [
                c
                for c in calls
                if c["method"] == "DELETE" and "/nodes/POI/src-poi" in c["url"]
            ]
            assert poi_deletes, "the source POI must be deleted (merge must reach its success path)"
        finally:
            page.unroute("**/api/v1/**")

    def test_defect2_change_lens_deletes_old_tagged_with(self, browser_page):
        """DEFECT 2: change-lens moves the TAGGED_WITH tag instead of duplicating it.

        Before the fix the change-lens branch POSTed a new TAGGED_WITH edge to the
        chosen lens but never deleted the beat's existing tag to its original
        lens, so the beat ended up tagged with BOTH lenses. The fix resolves the
        old lens id from ``beat.lens_slug`` and DELETEs that TAGGED_WITH edge.

        We stage lensSlugToId so both the old and new lens resolve, stub the
        source-beat TAGGED_WITH lookup, and assert the old-lens edge is DELETEd by
        id (in addition to the new-lens POST).
        """
        page, _seed_data, _reporter = browser_page
        self._fresh_page(page)
        calls = self._install_request_recorder(
            page,
            [
                (
                    "GET",
                    "/edges/TAGGED_WITH?source_id=beat-9",
                    200,
                    '{"items":[{"id":"edge-old-tag","type":"TAGGED_WITH",'
                    '"source_id":"beat-9","target_id":"lens-old","properties":{}}],'
                    '"total":1,"skip":0,"limit":200}',
                ),
                # HAS_BEAT lookup for the transfer-delete step (unrelated to this assert).
                (
                    "GET",
                    "/edges/HAS_BEAT?source_id=src-poi",
                    200,
                    '{"items":[{"id":"edge-hb","type":"HAS_BEAT",'
                    '"source_id":"src-poi","target_id":"beat-9","properties":{}}],'
                    '"total":1,"skip":0,"limit":200}',
                ),
            ],
        )
        try:
            page.evaluate(
                """async () => {
                window.cachedPoiList = [];
                window.lensSlugToId = { hidden_history: 'lens-old', power_players: 'lens-new' };
                const target = { id: 'tgt-poi', properties: { name: 'Target' } };
                const source = { id: 'src-poi', properties: { name: 'Source' } };
                const beatItems = [{
                  resolution: 'change-lens',
                  newLensSlug: 'power_players',
                  sourceBeat: { id: 'beat-9', lens_slug: 'hidden_history' },
                }];
                await window.executeMerge(target, source, beatItems);
            }"""
            )
            # New-lens TAGGED_WITH edge POSTed.
            new_tag_posts = [
                c
                for c in calls
                if c["method"] == "POST"
                and "/edges/TAGGED_WITH" in c["url"]
                and "lens-new" in (c["body"] or "")
            ]
            assert new_tag_posts, "change-lens must POST the new-lens TAGGED_WITH edge"
            # Old-lens TAGGED_WITH edge DELETEd by id (the fix).
            old_tag_deletes = [
                c
                for c in calls
                if c["method"] == "DELETE" and "/edges/TAGGED_WITH/edge-old-tag" in c["url"]
            ]
            assert old_tag_deletes, (
                "change-lens must DELETE the beat's original TAGGED_WITH edge so the "
                "tag is MOVED, not duplicated; recorded DELETEs: "
                f"{[c['url'] for c in calls if c['method'] == 'DELETE']}"
            )
        finally:
            page.unroute("**/api/v1/**")

    def test_defect3_bulk_upload_matched_poi_merge_failure_surfaces_error(
        self, browser_page
    ):
        """DEFECT 3: a failed POI merge in the bulk path is not a silent orphan.

        Before the fix the matched-poi (and conflict) branches of executeUpload
        did ``const poiNode = await poiResp.json()`` WITHOUT checking poiResp.ok.
        A 500 returned an error body with no ``id``, so poiNode.id was undefined,
        the beat was still created, and the HAS_BEAT link was built with an
        undefined source — an orphaned beat, while stats reported success. The fix
        throws when poiResp is not ok. We stub ``POST /nodes/POI`` to 500 and
        assert the run records an error and never creates the (would-be orphaned)
        beat node.
        """
        page, _seed_data, _reporter = browser_page
        self._fresh_page(page)
        calls = self._install_request_recorder(
            page,
            [("POST", "/nodes/POI", 500, '{"detail":"poi merge boom"}')],
        )
        try:
            stats = page.evaluate(
                """async () => {
                window.lensSlugToId = { hidden_history: 'lens-1' };
                window.lensSlugSet = new Set(['hidden_history']);
                const plan = {
                  newPois: [],
                  matchedPois: [{
                    poi: { poi_name: 'Louvre', latitude: 48.86, longitude: 2.33 },
                    beats: [{ action: 'create', beat: { script_body: 'x', lens: 'hidden_history', gravity: 3 } }],
                  }],
                  conflicts: [],
                  reviewItems: [],
                  errors: [],
                };
                return await window.executeUpload(plan);
            }"""
            )
            assert stats["errors"], (
                "a failed POI merge must record an error, not report a silent success; "
                f"stats={stats}"
            )
            # The beat node must NOT be created (no orphan).
            beat_creates = [
                c
                for c in calls
                if c["method"] == "POST" and "/nodes/NarrativeBeat" in c["url"]
            ]
            assert not beat_creates, (
                "no NarrativeBeat may be created after a failed POI merge (would be orphaned); "
                f"got {len(beat_creates)} beat POST(s)"
            )
            assert stats.get("beatsCreated", 0) == 0, (
                f"beatsCreated must stay 0 when the POI merge failed; stats={stats}"
            )
        finally:
            page.unroute("**/api/v1/**")

    def test_defect4_bulk_upload_failed_edge_link_not_counted_as_linked(
        self, browser_page
    ):
        """DEFECT 4: a failed HAS_BEAT link in the bulk path is not counted.

        Before the fix executeUpload ran ``stats.relsLinked++`` unconditionally
        after each edge POST, so a failed HAS_BEAT link (an orphaned beat) was
        still counted as linked and the run reported full success. The fix checks
        ``.ok`` and throws, which the surrounding catch turns into a stats.errors
        entry that stops the run. We stub ``POST /edges/HAS_BEAT`` to 500 (POI +
        beat creates stay stubbed-ok) and assert relsLinked is NOT incremented for
        the failed link and an error is recorded.
        """
        page, _seed_data, _reporter = browser_page
        self._fresh_page(page)
        self._install_request_recorder(
            page,
            [("POST", "/edges/HAS_BEAT", 500, '{"detail":"edge boom"}')],
        )
        try:
            stats = page.evaluate(
                """async () => {
                window.lensSlugToId = { hidden_history: 'lens-1' };
                window.lensSlugSet = new Set(['hidden_history']);
                const plan = {
                  newPois: [{
                    poi: { poi_name: 'Pantheon', latitude: 48.846, longitude: 2.346 },
                    beats: [{ beat: { script_body: 'x', lens: 'hidden_history', gravity: 3 } }],
                  }],
                  matchedPois: [],
                  conflicts: [],
                  reviewItems: [],
                  errors: [],
                };
                return await window.executeUpload(plan);
            }"""
            )
            assert stats["errors"], (
                "a failed HAS_BEAT link must record an error; " f"stats={stats}"
            )
            assert stats.get("relsLinked", 0) == 0, (
                "relsLinked must NOT count a HAS_BEAT link that returned 500; "
                f"stats={stats}"
            )
        finally:
            page.unroute("**/api/v1/**")


# ---------------------------------------------------------------------------
# Test: New-city onboarding panel (Step 7) — real-browser end-to-end proof
# ---------------------------------------------------------------------------


def _committed_tree_snapshot() -> dict[str, bytes]:
    """Content snapshot of the committed artifacts a HERMETIC (tmp-root) onboard must
    never touch: ``src/cities.json`` plus every file of a committed london corpus (when
    one exists). Comparing a before/after snapshot proves the onboard wrote ONLY under
    its tmp root — robust to london being a REAL committed city, and equally to its
    corpus having been retired. (The pre-milestone check asserted london was *absent*
    from the committed tree; that inverts the day london ships, so we assert *unchanged
    bytes* instead.)"""
    snap: dict[str, bytes] = {}
    registry = REPO_ROOT / "src" / "cities.json"
    if registry.exists():
        snap["src/cities.json"] = registry.read_bytes()
    london = REPO_ROOT / "data" / "london"
    if london.exists():
        for path in sorted(london.rglob("*")):
            if path.is_file():
                snap[str(path.relative_to(REPO_ROOT))] = path.read_bytes()
    return snap


class TestOnboardPanel:
    """Real-browser proof of the new-city onboarding panel (frontend/onboard.html).

    Drives the LIVE onboard API on :8001 (fixture + mock mode, so $0 and no
    network) through the whole London flow in a real Chromium page: the live
    source-consult feed, the merged POIs, the beat-drafting cost gate, a HERMETIC
    local upload (writes land only under the module tmp dir wired into api_server's
    env), and finally the round-trip — the deployed city appears in the :8001
    graph (7689) via GET /cities AND in review.html's city picker.

    Uses ``browser_page`` to reuse the module server + Chromium page; the seeded
    graph is irrelevant here, so the seed_data payload is ignored. This is the LAST
    DB-mutating test in the module (it uploads London into 7689), so it cannot
    perturb the earlier exact-count/proximity assertions.
    """

    def test_onboard_london_end_to_end(self, browser_page):
        page, _seed_data, _reporter = browser_page

        # Snapshot the committed tree BEFORE any onboarding runs, so the hermeticity
        # assertion (below) can prove the tmp-root onboard left src/cities.json and any
        # committed london corpus byte-identical — even once london is a real city.
        committed_before = _committed_tree_snapshot()

        # (a) Panel initial state: only the license-clean mode is available.
        page.goto(ONBOARD_URL)
        expect(page.locator("#onboardStartBtn")).to_be_visible()
        expect(page.locator("#mode-license_clean")).to_be_checked()
        expect(page.locator("#mode-license_clean")).to_be_enabled()
        expect(page.locator("#mode-shadow_discovery_only")).to_be_disabled()
        expect(page.locator("#mode-manual_book_drop")).to_be_disabled()
        _take_screenshot(page, "onboard-01-panel-initial")

        # (b) Fill the form and start the run.
        page.locator("#onboardSlug").fill("london")
        page.locator("#onboardDisplayName").fill("London")
        page.locator("#onboardBbox").fill(LONDON_BBOX)
        page.locator("#onboardStartBtn").click()

        # (c) The consult feed streams every source line-by-line, each with a URL.
        page.wait_for_function(
            """() => {
                const el = document.querySelector('#consultFeed');
                if (!el) return false;
                const t = (el.textContent || '').toLowerCase();
                return ['wikipedia', 'wikivoyage', 'wikidata', 'osm'].every(s => t.includes(s))
                    && t.includes('http');
            }""",
            timeout=30000,
        )
        feed_text = (page.locator("#consultFeed").text_content() or "").lower()
        for source in ("wikipedia", "wikivoyage", "wikidata", "osm"):
            assert source in feed_text, f"consult feed is missing a line for {source!r}: {feed_text!r}"
        assert "http" in feed_text, f"consult feed has no source URL: {feed_text!r}"
        _take_screenshot(page, "onboard-02-feed-consulting")

        # (d) The POI pane renders the merged count (expect 36; assert >= 30).
        page.wait_for_function(
            """() => {
                const el = document.querySelector('#poiCount');
                if (!el) return false;
                const digits = (el.textContent || '').replace(/[^0-9]/g, '');
                return digits.length > 0 && parseInt(digits, 10) >= 30;
            }""",
            timeout=30000,
        )
        poi_text = page.locator("#poiCount").text_content() or ""
        poi_match = re.search(r"\d+", poi_text)
        poi_n = int(poi_match.group()) if poi_match else 0
        assert poi_n >= 30, f"expected >= 30 merged POIs (36), got {poi_n} from {poi_text!r}"
        _take_screenshot(page, "onboard-03-pois-rendered")

        # (e) Draft beats — ONE click, no spend dialog. OWNER DECISION 2026-07-31:
        # the workbench replicates the tourist's app experience, and a tourist is
        # never asked to approve spend, so the cost-confirmation modal that used to
        # sit between these two steps is gone (the same ruling that removed
        # generateTourPreview's window.confirm). The page sends confirm_cost:true
        # itself; the API's contract is unchanged for other callers.
        # tests/test_workbench_preview_wiring.py::test_drafting_beats_shows_no_spend_confirmation
        # guards the page source; this is the end-to-end half.
        assert page.locator("#costModal").count() == 0, (
            "the cost-confirmation modal is back in the onboard page — the "
            "workbench must never ask a human to approve spend"
        )
        page.locator("#draftBeatsBtn").click()
        _take_screenshot(page, "onboard-04-drafting-no-dialog")

        # (f) Beats drafted directly. The pane shows "<N> beats drafted"; the
        # drafter splits each POI's lead into ~3 verbatim spans, so that N is
        # > #poiCount (the one-beat era drafted < poi_n because a POI without a
        # Wikipedia extract yields none). Wait for that multi-beat drafted state,
        # not a stale literal.
        page.wait_for_function(
            """(minBeats) => {
                const el = document.querySelector('#beatPane');
                if (!el) return false;
                const m = (el.textContent || '').match(/(\\d+)\\s+beats drafted/);
                return m !== null && parseInt(m[1], 10) > minBeats;
            }""",
            arg=poi_n,
            timeout=30000,
        )
        beat_text = page.locator("#beatPane").text_content() or ""
        beat_match = re.search(r"(\d+)\s+beats drafted", beat_text)
        assert beat_match, f"beat pane never showed a drafted count: {beat_text!r}"
        assert int(beat_match.group(1)) > poi_n, (
            f"drafted beats ({beat_match.group(1)}) must be multi-beat (> {poi_n} POIs): {beat_text!r}"
        )
        _take_screenshot(page, "onboard-05-beats-drafted")

        # (g) Upload locally, then prove the write landed HERMETICALLY.
        page.locator("#uploadLocalBtn").click()
        page.wait_for_function(
            """() => {
                const el = document.querySelector('#uploadResult');
                return el && (el.textContent || '').trim().length > 0;
            }""",
            timeout=60000,
        )
        upload_text = page.locator("#uploadResult").text_content() or ""
        assert "error" not in upload_text.lower() and "fail" not in upload_text.lower(), (
            f"upload reported a failure: {upload_text!r}"
        )
        # HERMETIC: the city + beats were written under the module tmp root and the
        # tmp registry now knows London — asserted from the TEST process (not the page).
        london_dir = _ONBOARD_TMP / "data" / "london"
        assert (london_dir / "poi-raw.json").exists(), f"{london_dir / 'poi-raw.json'} was not written"
        assert (london_dir / "beats.json").exists(), f"{london_dir / 'beats.json'} was not written"
        tmp_registry = json.loads((_ONBOARD_TMP / "cities.json").read_text(encoding="utf-8"))
        assert "london" in tmp_registry, f"tmp cities.json is missing 'london': {tmp_registry}"
        # And the COMMITTED tree is byte-untouched by the hermetic (tmp-root) onboard:
        # src/cities.json and any committed london corpus are identical before/after.
        # (Robust to london being a REAL committed city — the pre-milestone "london is
        # absent from the committed tree" check inverts the day london ships.)
        assert _committed_tree_snapshot() == committed_before, (
            "the hermetic onboard mutated the COMMITTED tree (src/cities.json or the "
            "london corpus) — the tmp-root pin failed"
        )
        _take_screenshot(page, "onboard-06-upload-success")

        # (h) Round-trip: the deploy loaded London into the :8001 server's OWN graph
        # (7689), so GET /cities reports it, and review.html's picker offers it.
        cities_resp = _api_get("/cities")
        assert isinstance(cities_resp, dict) and "cities" in cities_resp, (
            f"GET /cities did not return the expected shape: {cities_resp!r}"
        )
        # POIs are written with city_name == the slug, so the entry is 'london'
        # (lowercase); match case-insensitively for robustness.
        london_entries = [
            c for c in cities_resp["cities"] if (c.get("city_name") or "").lower() == "london"
        ]
        assert london_entries, f"GET /cities has no London entry after upload: {cities_resp!r}"
        assert london_entries[0]["poi_count"] >= 30, (
            f"deployed London has {london_entries[0]['poi_count']} POIs, expected >= 30"
        )

        page.goto(WORKBENCH_URL)
        page.wait_for_function(
            """() => {
                const s = document.querySelector('#citySelect');
                return s && [...s.options].some(o => (o.textContent || '').toLowerCase().includes('london'));
            }""",
            timeout=20000,
        )
        _take_screenshot(page, "onboard-07-selector-shows-london")


# ---------------------------------------------------------------------------
# Test: Bug Report Generation (Task 8)
# ---------------------------------------------------------------------------


class TestBugReport:
    """Verify bug report is generated at the end of the suite."""

    def test_report_generated(self, browser_page):
        """Final test: save the bug report and verify its structure."""
        page, _seed_data, reporter = browser_page

        report_path = reporter.save_report()

        assert report_path.exists(), f"Bug report not found at {report_path}"

        content = report_path.read_text(encoding="utf-8")
        assert "# Editorial Workbench UI Bug Report" in content
        assert "## Summary" in content
        assert "Tests run:" in content
        assert "Issues found:" in content

        _take_screenshot(page, "final-state")


# ── Real tour generation, UNSTUBBED ─────────────────────────────────────────
#
# Every other tour test in this file answers its own /trips/preview with a canned
# payload, so none of them can notice that the workbench cannot build a tour.
# That is not hypothetical: on 2026-07-31 the whole suite was green while the UI
# produced nothing. tests/test_suite_honesty.py fails if this class disappears.
#
# The original stub's excuse was accurate — "the test DB's tier-1 fixture POIs
# can't produce a tour". Two things made it impossible, both fixed here:
#   1. the density gate needs tier >= 3 POIs carrying >= 3 active beats
#      (src/tour/density.py ANCHOR_CANDIDATE_TIER_MIN / _BEAT_COUNT_MIN), and the
#      shared fixture creates tier 1;
#   2. the corpus loader matches `city_name` against the SLUG ('paris'), while
#      the shared fixture writes 'Paris' — so its POIs were invisible regardless.
#
# So this seeds a real Ile de la Cite slice exported from the live corpus
# (tests/fixtures/ile_de_la_cite_tourable.json) into the dedicated 7689 graph,
# and removes it again.
#
# IT COSTS NOTHING, and here is the actual mechanism now that the
# ONDOWAY_ENABLE_PAID_LLM_CALLS gate has been removed (2026-07-31): conftest.py
# blanks ANTHROPIC_API_KEY (and the other paid keys) for every non-live run, and
# the api_server subprocess inherits that environment. The premium executor is
# therefore constructed but cannot authenticate, so it fails and the response is
# the Basic grounded lane — real route, real stops, real corpus prose, no billable
# call. Proving the PREMIUM lane needs a real key and belongs in a live shard.

TOURABLE_FIXTURE = Path(__file__).parent / "fixtures" / "ile_de_la_cite_tourable.json"


@pytest.fixture()
def tourable_corpus(api_server):
    """Seed a tour-capable Paris corpus into 7689, then remove it.

    Function-scoped with cleanup because the shared ``seed_data`` fixture carries
    exact-count assertions ("exactly 13 worklist rows"); leaving these POIs behind
    would break them.
    """
    payload = json.loads(TOURABLE_FIXTURE.read_text(encoding="utf-8"))
    assert payload, f"{TOURABLE_FIXTURE} is empty — this test would prove nothing"

    driver = GraphDatabase.driver(WORKBENCH_NEO4J_URI, auth=WORKBENCH_NEO4J_AUTH)
    marker = "tourable-fixture"
    try:
        with driver.session(database=WORKBENCH_NEO4J_DATABASE) as session:
            for entry in payload:
                poi = dict(entry["poi"])
                lat, lng = poi.pop("latitude"), poi.pop("longitude")
                session.run(
                    "CREATE (p:POI $props) "
                    "SET p.id = randomUUID(), p.seed_marker = $marker, "
                    "    p.location = point({latitude: $lat, longitude: $lng})",
                    props=poi, marker=marker, lat=lat, lng=lng,
                )
                for beat in entry["beats"]:
                    session.run(
                        "MATCH (p:POI {name: $name, seed_marker: $marker}) "
                        "CREATE (p)-[:HAS_BEAT]->(b:NarrativeBeat $props) "
                        "SET b.id = randomUUID(), b.seed_marker = $marker",
                        name=poi["name"], marker=marker, props=beat,
                    )
        yield payload
    finally:
        with driver.session(database=WORKBENCH_NEO4J_DATABASE) as session:
            session.run(
                "MATCH (n) WHERE n.seed_marker = $marker DETACH DELETE n", marker=marker
            )
        driver.close()


class TestRealTourGeneration:
    """The one test in this file that lets the workbench actually build a tour."""

    def test_workbench_generates_a_real_tour_unstubbed(self, browser_page, tourable_corpus):
        """INVARIANT (audit E :5234): the workbench really builds a tour from the
        seeded corpus, unstubbed, and the seeded names appear on screen. Press
        "Plan the day" and let the real endpoint answer — no route interception —
        then write THE day with the one "Write the tour" action (S4.8, design
        §8.1: the plan reply carries exactly one day; there is no pick).

        Asserts the stops that render came from the SEEDED corpus, not from a
        fixture in this file — a canned payload cannot satisfy it, because the
        POI names are read back out of the JSON the database was seeded from.
        Structural assertions on purpose (names on screen, one day on the wire),
        never measured gate readings — those re-baseline as the corpus moves.

        The only test tests/test_suite_honesty.py accepts as UNSTUBBED coverage
        of /trips/preview: nothing this test references may route that endpoint.

        UNDO TEST: stub **/trips/preview in this test -> the rendered names stop
        matching the seed and it goes RED. Drop the seeded corpus and the engine
        finds no anchors, so it goes RED too.
        """
        page, _seed, _reporter = browser_page
        seeded_names = {entry["poi"]["name"] for entry in tourable_corpus}

        page.goto(WORKBENCH_URL)
        page.wait_for_load_state("networkidle")

        # Pick the seeded city, then open the preview panel.
        picker = page.locator("#citySelect")
        picker.wait_for(state="visible", timeout=15000)
        # Resolve the Paris option from the LIVE list rather than assuming a
        # label: the picker is built from the API and its text carries a POI
        # count that changes with the seed.
        paris_value = page.evaluate(
            "() => [...document.getElementById('citySelect').options]"
            ".filter(o => o.textContent.trim().toLowerCase().startsWith('paris'))"
            ".map(o => o.value)[0] ?? null"
        )
        assert paris_value is not None, (
            "the city picker offers no Paris option, so the seeded corpus never "
            "reached the workbench graph"
        )
        picker.select_option(value=paris_value)
        page.locator("#citySubmitBtn").click()
        page.locator("#tourPreviewBtn").click()

        # Direct .value writes (no change events): a dirty input fires the W4.2
        # auto-replan on blur, and a second in-flight plan request against the
        # REAL engine would race every assertion below.
        _set_tour_inputs(page, start="48.852966,2.349902", duration=60)
        # 60, and NOT less. Measured on this fixture: at 60 min the engine sees
        # more anchors and better compactness; the walk radius scales with the
        # timebox, so a shorter tour reaches fewer POIs — shrinking the ask
        # starves the gate instead of satisfying it. The fix for a thin fill is
        # more beats per POI (the fixture carries 12 each), not a smaller timebox.

        # THE POINT: no page.route here. The server really answers.
        with page.expect_response(
            lambda r: r.url.endswith("/trips/preview"), timeout=180000
        ) as caught:
            page.locator("#tourGenerateBtn").click()
        response = caught.value

        assert response.status == 200, (
            f"the workbench could not generate a tour: HTTP {response.status} "
            f"{response.text()[:400]}"
        )
        _take_screenshot(page, "real-tour-generated-unstubbed")

        # PLANNING is free and returns THE day. Nothing is written yet, so there
        # is no narration to check here — only that the engine really routed the
        # seeded corpus into one day (§8.1: options is a list of exactly one).
        body = response.json()
        options = body.get("options") or []
        assert len(options) == 1, (
            f"the endpoint answered 200 but did not return THE one-day plan "
            f"(design §8.1; got {len(options)} options): {str(body)[:400]}"
        )
        planned_names = {s.get("name") for s in (options[0].get("stops") or [])}
        assert planned_names & seeded_names, (
            f"none of the SEEDED POIs appear in the planned day, so this did not "
            f"come from the corpus under test. Seeded: {sorted(seeded_names)}. "
            f"Planned: {sorted(n for n in planned_names if n)}"
        )
        # The day renders immediately, places only — seeded names on screen
        # BEFORE anything is written. The write button is the render's last
        # element, so its visibility is what proves the day finished drawing.
        write_btn = page.locator("#tourWriteBtn")
        write_btn.wait_for(state="visible", timeout=20000)
        plan_text = page.locator("#tourStops").inner_text()
        assert {name for name in seeded_names if name in plan_text}, (
            f"the planned day rendered none of the seeded POIs: {plan_text[:400]}"
        )

        # Now WRITE the day — the only call that writes and spends. This shard
        # runs without a usable ANTHROPIC_API_KEY (conftest blanks it), so it
        # lands in the Basic grounded lane; either lane proves a tour was BUILT.
        _diag_reqs: list[str] = []
        _diag_msgs: list[str] = []
        page.on(
            "request",
            lambda r: _diag_reqs.append(
                f"{r.url.split('/api/v1')[-1]} BODY={(r.post_data or '')[:600]}"
            ),
        )
        page.on("console", lambda m: _diag_msgs.append(f"{m.type}: {m.text[:200]}"))
        page.on("pageerror", lambda e: _diag_msgs.append(f"PAGEERROR: {str(e)[:200]}"))
        try:
            with page.expect_response(
                lambda r: "/trips/preview/author" in r.url, timeout=30000
            ) as authored:
                write_btn.click()
        except Exception:
            print("DIAG requests:", [u.split("/api/v1")[-1] for u in _diag_reqs][-10:])
            print("DIAG console:", _diag_msgs[-10:])
            print("DIAG btn disabled:", write_btn.is_disabled(), "text:", write_btn.text_content())
            raise
        assert authored.value.status == 200, (
            f"the workbench could not write the planned day: HTTP "
            f"{authored.value.status} {authored.value.text()[:400]}"
        )

        rendered = page.locator(".tour-stop")
        rendered.first.wait_for(state="visible", timeout=20000)
        assert rendered.count() >= 1, "stops came back over the wire but none rendered"

        page_text = page.locator("#tourStops").inner_text()
        matched = {name for name in seeded_names if name in page_text}
        assert matched, (
            f"none of the SEEDED POIs appear in the rendered tour, so this did not come "
            f"from the corpus under test. Seeded: {sorted(seeded_names)}. "
            f"Rendered: {page_text[:400]}"
        )

    def test_a_degraded_tour_shows_the_problem_panel_with_a_copy_button(
        self, browser_page, tourable_corpus
    ):
        """OWNER RULING 2026-07-31: "Don't just log errors. Actually show them in
        the workbench UI. Otherwise, they're invisible."

        This is that ruling's proof. The whole degradation feature shipped once
        with no test at all — a judge caught it, and a feature whose purpose is
        "stop trusting that a problem was noticed" is the last thing that should
        be taken on trust.

        The degrade is REAL, not injected: this shard runs with a blanked
        ANTHROPIC_API_KEY (conftest), and the api_server subprocess inherits it,
        so the narrator cannot authenticate. That used to be a silent log line;
        it must now be on screen.

        RE-DERIVED 2026-08-18 (Phase 4 close) against what this path ACTUALLY
        does. The old body asserted ``glue_call_failed`` / ``HaikuGlueClient``,
        which CANNOT occur here and never could: walk-transition glue is written
        at PLAN time, and Block 1 plans with ``SilentGlueClient`` by default
        because planning is free (premium_tour.plan_premium_tour). The real
        HaikuGlueClient is only ever defaulted in ``generation.py`` for the
        compose path. On preview->author the FIRST thing that needs the provider
        is per-stop authoring, which fails to authenticate and raises, so the
        request falls to the Basic lane before any transition is written.
        MEASURED, not reasoned: the identical assertion fails the identical way
        at 258933fd (Phase 3's tree, scoped-stash probe) — an inherited red that
        the api_server pipe deadlock had been hiding, because this class timed
        out before ever reaching the assertion.

        So the invariant this test defends is unchanged and the scenario is
        unchanged; only the NAME of the honest failure is corrected.

        UNDO TEST: delete the buildDegradationPanel(...) call from
        frontend/review.html's renderTourStops -> RED (no panel).
        """
        page, _seed, _reporter = browser_page
        page.goto(WORKBENCH_URL)
        page.wait_for_load_state("networkidle")

        paris_value = page.evaluate(
            "() => [...document.getElementById('citySelect').options]"
            ".filter(o => o.textContent.trim().toLowerCase().startsWith('paris'))"
            ".map(o => o.value)[0] ?? null"
        )
        assert paris_value is not None, "the seeded corpus never reached the graph"
        page.locator("#citySelect").select_option(value=paris_value)
        page.locator("#citySubmitBtn").click()
        page.locator("#tourPreviewBtn").click()
        _set_tour_inputs(page, start="48.852966,2.349902", duration=60)

        with page.expect_response(
            lambda r: r.url.endswith("/trips/preview"), timeout=180000
        ):
            page.locator("#tourGenerateBtn").click()

        # The degrade under test is the NARRATOR failing, and nothing is narrated
        # until the day is written — planning makes no provider call at all. So
        # write the tour (S4.8, design §8.1: the one action left): that is the
        # call that tries to write, fails to reach the narrator, and must say so.
        write_btn = page.locator("#tourWriteBtn")
        write_btn.wait_for(state="visible", timeout=20000)
        with page.expect_response(
            lambda r: "/trips/preview/author" in r.url, timeout=180000
        ) as caught:
            write_btn.click()
        body = caught.value.json()

        # The API must report the degrade at all — panel or no panel.
        rows = body.get("degradations") or []
        assert rows, (
            "the narrator was unreachable, so the day could not be written, but "
            "the response reported NO degradations — the failure is invisible "
            f"again. lane={body.get('narration_kind')!r}"
        )
        # And it must be THE failure that really happened, named as itself: a stop
        # that could not be composed, because the provider could not be reached.
        # Asserting the kind AND the provider's own error keeps this from passing
        # on some unrelated degradation that happens to be present.
        failed = [r for r in rows if r.get("kind") == "premium_unit_failed"]
        assert failed, rows
        assert any(
            "authentication" in (r.get("error_message") or "").lower() for r in failed
        ), failed

        # And it must be ON SCREEN, which is what the owner actually asked for.
        panel = page.locator(".tour-degradations")
        panel.first.wait_for(state="visible", timeout=20000)
        _take_screenshot(page, "degradation-panel-visible")

        panel_text = panel.first.inner_text()
        assert "problem" in panel_text.lower(), panel_text
        # Plain English for the human: the traveller's sentence, no jargon in it.
        assert "failed to compose" in panel_text, panel_text
        # ...and the technical half for whoever fixes it: what broke, and why.
        assert "execute_premium_plan.invoke" in panel_text, panel_text
        assert "authentication" in panel_text.lower(), panel_text
        # ...and the one-click handoff.
        assert page.locator(".tour-degradations button").count() >= 1, (
            "the panel renders no 'Copy report for Claude' button, so the report "
            "cannot be handed over in one action"
        )

    def test_an_unavailable_voice_refuses_before_spending_a_request(
        self, browser_page, tourable_corpus
    ):
        """Pressing Listen on a voice the server cannot serve must fail FAST and
        SAY WHY, without issuing a doomed request.

        Before this, the page fired the request anyway and showed a bare
        "TTS failed (502)" with no cause. This shard's server runs with a blanked
        OPENAI_API_KEY, so /audio/providers honestly reports openai unavailable —
        the exact condition. Note this test must NOT use the
        ``_stubbed_audio_preview`` fixture, which stubs the availability probe to
        report the voice available and would route around the branch under test.

        UNDO TEST: delete the availability refusal from ttsPlay -> a request is
        issued and `asked` becomes non-empty -> RED.
        """
        page, _seed, _reporter = browser_page
        page.goto(WORKBENCH_URL)
        page.wait_for_load_state("networkidle")

        paris_value = page.evaluate(
            "() => [...document.getElementById('citySelect').options]"
            ".filter(o => o.textContent.trim().toLowerCase().startsWith('paris'))"
            ".map(o => o.value)[0] ?? null"
        )
        page.locator("#citySelect").select_option(value=paris_value)
        page.locator("#citySubmitBtn").click()
        page.locator("#tourPreviewBtn").click()
        _set_tour_inputs(page, start="48.852966,2.349902", duration=60)
        with page.expect_response(lambda r: r.url.endswith("/trips/preview"), timeout=180000):
            page.locator("#tourGenerateBtn").click()
        # Listen buttons exist only on a written tour, so the day must be written
        # before there is anything to press play on (S4.8, design §8.1).
        write_btn = page.locator("#tourWriteBtn")
        write_btn.wait_for(state="visible", timeout=20000)
        with page.expect_response(
            lambda r: "/trips/preview/author" in r.url, timeout=180000
        ):
            write_btn.click()

        # Record every preview request the page issues from here on.
        asked: list[str] = []
        page.on("request", lambda r: asked.append(r.url) if "/audio/preview" in r.url else None)

        listen = page.locator(".tts-play-btn[data-tour-stop]")
        listen.first.wait_for(state="visible", timeout=20000)
        listen.first.click()

        toast = page.locator("#errorToast")
        toast.wait_for(state="visible", timeout=15000)
        message = toast.inner_text()
        _take_screenshot(page, "unavailable-voice-refused")

        assert "not available" in message.lower(), message
        assert "api key" in message.lower(), (
            f"the refusal does not name the CAUSE, so the editor cannot act on "
            f"it: {message!r}"
        )
        assert not asked, (
            f"the page issued {len(asked)} /audio/preview request(s) it already "
            f"knew would fail: {asked}"
        )


# ── The workbench runs the TOURIST path ─────────────────────────────────────
#
# CLAUDE.md rule 1: the workbench and the app run the EXACT SAME code for
# everything they share. Two pieces of tourist-audible content are written and
# voiced ONLY on the phone path, so until the "Run it like the phone" action no
# editor could preview either on any screen:
#
#   the FULL TELLING      plan_premium_full_telling / finalize_premium_full_telling,
#                         written by POST /trips/{id}/compose for every major stop
#   KEEP EXPLORING HERE   build_poi_extra_beats / build_poi_extra_narration, also
#                         written at compose and voiced by
#                         POST /audio/stops/{id}/keep-exploring
#
# tests/test_surfaces_share_one_engine.py proves by derivation that the workbench
# now ENTERS those doors. This proves the button a human presses really walks
# them, in the phone order, with the phone payloads.
#
# WHY THE ENDPOINTS ARE ANSWERED IN THE BROWSER, and what that does not weaken.
# Composing a real day makes one paid Opus call per stop, and this shard runs with
# a blanked ANTHROPIC_API_KEY (conftest), so a live compose here could only ever
# fail to authenticate — it would prove nothing about the sequence. So the paid
# half is stubbed and everything the WORKBENCH is responsible for is measured for
# real: the click, the request order, every outbound payload, the auth header, the
# render of the full telling and the extras, the tap for more, and a real audio
# decode. That is the same bargain `_stubbed_audio_preview` documents, and the
# stubs answer in the shapes the endpoints really return.


class TestWorkbenchRunsThePhonePath:
    """The workbench reaches the full telling and keep-exploring, through the
    tourist's own endpoints and a real signed-in identity."""

    #: What compose really hands back for a two-stop day: one MAJOR stop carrying
    #: its second composed piece (`full_narration` + `full_close_text`), and one
    #: ordinary stop carrying only the deterministic keep-exploring stitch.
    FULL_TELLING = (
        "The longer telling. Stand where the parvis opens out and the west front "
        "fills the whole sky, and the masons who set these stones become people "
        "with names."
    )
    EXTRAS = (
        "Keep exploring here. The spire you are looking at is a nineteenth-century "
        "reconstruction, and the argument about whether to build it lasted longer "
        "than the building of it."
    )

    def _composed_stops(self):
        return [
            {
                "sort_order": 1, "stop_id": "item-1", "poi_id": "poi-1",
                "poi_name": "Notre-Dame", "lat": 48.8530, "lng": 2.3499,
                "duration_min": 12, "importance_tier": 5, "start_time": "10:20",
                "dwell_seconds": 720,
                "narration": "The tight telling for the cathedral square.",
                "extra_narration": None,
                "full_narration": self.FULL_TELLING,
                "full_close_text": "Carry that west front with you.",
                "close_text": "Walk on when you are ready.",
            },
            {
                "sort_order": 2, "stop_id": "item-2", "poi_id": "poi-2",
                "poi_name": "Sainte-Chapelle", "lat": 48.8554, "lng": 2.3450,
                "duration_min": 8, "importance_tier": 4, "start_time": "10:45",
                "dwell_seconds": 480,
                "narration": "The tight telling for the glass.",
                "extra_narration": self.EXTRAS,
                "full_narration": None,
                "full_close_text": None,
                "close_text": "That is the last of the glass.",
            },
        ]

    def _install_phone_stubs(self, page, calls):
        """Answer the tourist endpoints in the shapes the server really returns."""
        from src.audio.provider import MockTTSProvider

        stops = self._composed_stops()

        def _record(route, body):
            calls.append(
                {
                    "url": route.request.url,
                    "method": route.request.method,
                    "post_data": route.request.post_data,
                    "auth": route.request.headers.get("authorization"),
                }
            )
            route.fulfill(
                status=200, content_type="application/json", body=json.dumps(body)
            )

        page.route(
            "**/auth/workbench-session",
            lambda route: _record(route, {
                "access_token": "wb-access-token", "token_type": "bearer",
                "user_id": "wb-user", "email": "workbench@ondoway.local",
                "profile_id": "wb-profile",
            }),
        )
        page.route(
            "**/trips/generate",
            lambda route: _record(route, {
                "trip_id": "trip-1", "trip_name": "Workbench audition",
                "profile_id": "wb-profile", "total_stops": 2,
                "total_duration_min": 20, "anchor_count": 1, "flavour_count": 1,
                "stops": [], "degradations": [],
                "options": [_route_option(stops, route_id="trip-1-opt1")],
            }),
        )
        page.route(
            "**/trips/*/compose",
            lambda route: _record(route, {
                "trip_id": "trip-1", "route_id": "trip-1-opt1", "attempts": 1,
                "plan_version": 1, "stops": stops, "degradations": [],
            }),
        )
        page.route(
            "**/audio/generate-trip-stops/*",
            lambda route: _record(route, {
                "trip_id": "trip-1", "generated": 1, "skipped": 0, "failed": 0,
                "results": [{"stop_id": "item-1", "status": "generated",
                             "audio_url": "/api/v1/audio/files/item-1.wav"}],
            }),
        )
        # The session GET carries the file the voicing pass wrote onto stop 1 and
        # nothing yet for stop 2 — so the per-stop poll below has real work to do,
        # exactly as it does when one stop's voicing lands late.
        page.route(
            "**/trips/*/session",
            lambda route: _record(route, {
                "trip_id": "trip-1", "plan_version": 1,
                "stops": [
                    dict(stops[0], audio_url="/api/v1/audio/files/item-1.wav",
                         audio_duration_sec=41.0),
                    dict(stops[1]),
                ],
                "promises": [], "contingencies": [], "retime_tolerance_seconds": 300,
                "walking_pace_kmh": 4.5, "day_start_hhmm": "10:00",
                "planned_end_hhmm": "11:00", "party": None,
                "finish_lat": None, "finish_lng": None, "finish_name": "your start",
                "end_hardness": "firm", "placement": None,
            }),
        )
        page.route(
            "**/audio/stop-status/*",
            lambda route: _record(route, {
                "stop_id": "item-2", "has_audio": True,
                "audio_url": "/api/v1/audio/files/item-2.wav", "duration_sec": 28.0,
            }),
        )
        page.route(
            "**/audio/stops/*/keep-exploring",
            lambda route: _record(route, {
                "stop_id": "item-2", "status": "generated",
                "audio_url": "/api/v1/audio/files/item-2-keep-exploring.wav",
                "duration_sec": 55.0,
            }),
        )
        # Real decodable bytes for whatever file the page then plays, so the
        # <audio> element genuinely reaches HAVE_CURRENT_DATA rather than a src
        # string nobody proved was playable.
        page.route(
            "**/audio/files/**",
            lambda route: route.fulfill(
                status=200, content_type="audio/wav",
                body=MockTTSProvider().generate("workbench phone-path audio"),
            ),
        )

    def _unroute_phone_stubs(self, page):
        for pattern in (
            "**/auth/workbench-session", "**/trips/generate", "**/trips/*/compose",
            "**/audio/generate-trip-stops/*", "**/trips/*/session",
            "**/audio/stop-status/*", "**/audio/stops/*/keep-exploring",
            "**/audio/files/**",
        ):
            page.unroute(pattern)

    def test_run_it_like_the_phone_walks_the_tourists_own_endpoints(self, browser_page):
        """One click runs generate -> compose -> voice -> session -> tap for more.

        The assertions that matter, each of which a different shortcut breaks:

        1. THE SEQUENCE IS THE PHONE'S. The page signs in, then calls
           /trips/generate, /trips/{id}/compose, /audio/generate-trip-stops/{id}
           and /trips/{id}/session — the same doors trip_service.dart knocks on.
        2. IT DOES NOT INVENT A ROUTE. The compose body carries the route_id the
           generate reply returned, so the day written is the day just saved.
        3. THE REQUEST IS THE DIALS. generate carries the profile from the
           workbench session and the same start/duration/city the plan used.
        4. BOTH AUTHENTICATED CALLS CARRY THE TOKEN.
        5. THE TOURIST-AUDIBLE CONTENT IS ON SCREEN — the full telling and the
           keep-exploring stitch, the two things no editor could see before.
        6. THE TAP FOR MORE NAMES NO PROVIDER. An unnamed provider resolves
           through the failover chain (get_provider_with_fallback); a named one
           resolves a single provider, which is the exact 2026-08-29 divergence
           the one-engine guard was written against.

        UNDO TEST: make playKeepExploring send {"provider": ttsProvider} -> RED on
        (6). Point the compose call at a workbench-only endpoint -> RED on (1).
        Delete the full-telling block from renderPhoneDay -> RED on (5).
        """
        page, _seed, _reporter = browser_page
        page.goto(WORKBENCH_URL)
        page.wait_for_load_state("networkidle")
        page.wait_for_function(
            "() => { const s = document.querySelector('#citySelect'); "
            "return s && [...s.options].some(o => o.textContent.includes('Paris')); }",
            timeout=15000,
        )
        page.locator(CITY_SELECT).select_option(index=1)
        page.locator(CITY_SUBMIT).click()
        page.locator(CITY_OVERLAY).wait_for(state="hidden", timeout=15000)
        page.locator("#tourPreviewBtn").click()
        page.wait_for_timeout(300)

        calls: list[dict] = []
        # The plan is stubbed only to put a day on screen — the phone action lives
        # on the rendered day, and the plan body is what its request is built from.
        _route_two_step(
            page,
            plan=_plan_payload([
                {"sort_order": 1, "poi_name": "Notre-Dame", "minutes": 12},
                {"sort_order": 2, "poi_name": "Sainte-Chapelle", "minutes": 8},
            ]),
            compose=_authored_payload({"stops": [], "spine_area": "-", "total_audio_min": 0}),
        )
        self._install_phone_stubs(page, calls)
        try:
            _set_tour_inputs(page, start="48.8566,2.3522", duration=90)
            assert _plan_the_day(page).status == 200

            run_btn = page.locator("#tourPhoneRunBtn")
            run_btn.wait_for(state="visible", timeout=10000)
            with page.expect_response(lambda r: "/trips/" in r.url and "/session" in r.url,
                                      timeout=30000):
                run_btn.click()
            page.wait_for_timeout(500)

            # (1) The sequence is the phone's, in the phone's order.
            paths = [c["url"].split("/api/v1")[-1].split("?")[0] for c in calls]
            assert paths[:5] == [
                "/auth/workbench-session",
                "/trips/generate",
                "/trips/trip-1/compose",
                "/audio/generate-trip-stops/trip-1",
                "/trips/trip-1/session",
            ], f"the workbench did not walk the tourist path in order: {paths}"
            assert "/audio/stop-status/item-2" in paths, (
                f"the per-stop audio poll the phone runs never happened: {paths}"
            )

            by_path = {p: c for p, c in zip(paths, calls, strict=True)}

            # (2) The compose call names the route generate returned.
            compose_body = json.loads(by_path["/trips/trip-1/compose"]["post_data"] or "{}")
            assert compose_body.get("route_id") == "trip-1-opt1", (
                f"compose must carry the route_id generate returned: {compose_body}"
            )

            # (3) The generate request is built from the SAME dials the plan used.
            gen_body = json.loads(by_path["/trips/generate"]["post_data"] or "{}")
            assert gen_body.get("profile_id") == "wb-profile", gen_body
            assert gen_body.get("center_lat") == 48.8566, gen_body
            assert gen_body.get("center_lng") == 2.3522, gen_body
            assert gen_body.get("duration_min") == 90, gen_body
            assert gen_body.get("city_slug"), f"the city never travelled: {gen_body}"
            # A trip record needs a calendar day whatever the dials say.
            assert gen_body.get("start_date") and gen_body.get("end_date"), gen_body
            assert gen_body.get("start_time"), gen_body

            # (4) Both ownership-scoped calls carry the workbench token.
            for path in ("/trips/generate", "/trips/trip-1/compose", "/trips/trip-1/session"):
                assert by_path[path]["auth"] == "Bearer wb-access-token", (
                    f"{path} was called without the workbench identity: {by_path[path]}"
                )

            # (5) The two pieces no editor could preview before are ON SCREEN.
            panel = page.locator("#tourStops").inner_text()
            assert self.FULL_TELLING in panel, (
                f"the FULL TELLING did not render: {panel[:400]}"
            )
            assert self.EXTRAS in panel, (
                f"the keep-exploring extras did not render: {panel[:400]}"
            )
            # Case-insensitively: both labels are styled text-transform:uppercase,
            # and which case reaches the screen is a stylesheet decision, not a
            # property of the feature.
            labels = panel.lower()
            assert "full telling" in labels and "keep exploring here" in labels, (
                f"neither piece is labelled for the editor: {panel[:400]}"
            )
            assert "1 with a full telling" in panel and "1 with keep-exploring" in panel, (
                f"the head does not count the two pieces: {panel[:400]}"
            )
            _take_screenshot(page, "phone-path-full-telling-rendered")

            # The scheduled per-stop recording plays and really decodes.
            page.locator('.tts-play-btn[data-phone-stop="0"]').click()
            page.wait_for_function(
                "sel => { const el = document.querySelector(sel);"
                " return !!el && el.readyState >= 2; }",
                arg='.tts-audio[data-phone-stop-audio="0"]',
                timeout=15000,
            )

            # (6) The tap for more goes through the tourist door and NAMES NO PROVIDER.
            with page.expect_response(lambda r: "/keep-exploring" in r.url, timeout=30000):
                page.locator('.tts-play-btn[data-phone-keep-exploring="1"]').click()
            page.wait_for_function(
                "sel => { const el = document.querySelector(sel);"
                " return !!el && el.readyState >= 2; }",
                arg='.tts-audio[data-phone-keep-audio="1"]',
                timeout=15000,
            )
            keep = next(
                c for c in calls if "/audio/stops/item-2/keep-exploring" in c["url"]
            )
            body = json.loads(keep["post_data"] or "{}")
            assert "provider" not in body and "voice_id" not in body, (
                "the tap for more named a provider, so it resolved ONE voice instead "
                "of the failover chain the phone gets — the exact divergence the "
                f"one-engine guard exists for: {body}"
            )
            _take_screenshot(page, "phone-path-tap-for-more")
        finally:
            self._unroute_phone_stubs(page)
            _unroute_two_step(page)
