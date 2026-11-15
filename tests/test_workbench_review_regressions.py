"""Real-browser regression guards for workbench data-loss and false-success defects.

Every case here drives the REAL web/workbench/review.html in headless Chromium with
the API stubbed at the network layer (``page.route``), so each test reproduces a
confirmed data-loss / false-success defect deterministically and at zero cost:
no Neo4j, no API server, no paid TTS. Each test was written RED against the
pre-fix file and only passes with the fix in place.

Covered defects (all in web/workbench/review.html):
  1  "Same place" upload demoted an existing POI's importance_tier to 1.
  2  "Same place" replaced name_variations instead of appending to it.
  3  Merge "Skip" orphaned the source beat after the source POI was deleted.
  4  Merge preview read an API error as "source POI has no beats" and offered
     to delete it.
  5  renderDetail() re-registered its delegated listeners on every render, so
     the count doubled per click (and one Listen click issued N paid TTS calls).
  6  runBeatConflictDetection ignored response status — an API error silently
     reported zero conflicts.
  7  Bulk-upload conflict detection skipped the same check and duplicated beats.
  8  POI cache pagination truncated silently, defeating duplicate detection.
  9  uploadSinglePoi validated beat lenses mid-loop, after earlier writes had
     already committed.

This suite is deliberately independent of tests/test_workbench_ui.py: it needs
neither the dedicated workbench Neo4j (7689) nor the :8001 API, so it can run
alongside anything. Run it with:

    uv run pytest tests/test_workbench_review_regressions.py -o addopts= -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright

from tests.browser_launch import chromium_launch_options

REVIEW_HTML = (Path(__file__).parent.parent / "web" / "workbench" / "review.html").resolve()
REVIEW_URL = REVIEW_HTML.as_uri()

# Records every addEventListener registration together with its AbortSignal, so
# DEFECT 5 can be measured directly: how many delegated #detailBody click
# listeners are still LIVE (signal absent or not aborted) after N renders.
LISTENER_SPY = """
(() => {
  window.__listenerLog = [];
  const orig = EventTarget.prototype.addEventListener;
  EventTarget.prototype.addEventListener = function (type, fn, opts) {
    try {
      window.__listenerLog.push({
        id: (this && this.id) || '',
        type: type,
        opts: opts || null,
      });
    } catch (e) { /* never break the page for the spy */ }
    return orig.call(this, type, fn, opts);
  };
  window.__liveDetailClickListeners = function () {
    return window.__listenerLog.filter(function (r) {
      if (r.id !== 'detailBody' || r.type !== 'click') return false;
      const sig = r.opts && r.opts.signal;
      return !sig || !sig.aborted;
    }).length;
  };
})();
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(**chromium_launch_options())
        yield b
        b.close()


@pytest.fixture
def page(browser):
    """A freshly loaded workbench with every API call stubbed and RECORDED.

    ``page.__requests__`` accumulates ``(method, url)`` for every intercepted
    call, which is how the destructive-write assertions below are made: a test
    proves a POI/beat was never deleted or created by proving the request was
    never issued.
    """
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.add_init_script(LISTENER_SPY)
    requests: list[tuple[str, str]] = []
    pg.__requests__ = requests  # type: ignore[attr-defined]

    def default_route(route):
        requests.append((route.request.method, route.request.url))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"items": [], "total": 0, "beats": [], "cities": []}),
        )

    pg.route("**/api/v1/**", default_route)
    pg.goto(REVIEW_URL)
    pg.wait_for_function("() => typeof window.mapPoiForApi === 'function'")
    pg.evaluate("() => { window.cityName = 'paris'; }")
    yield pg
    ctx.close()


def _stub(pg, pattern, handler):
    """Install a recording route that overrides the module-level default."""

    def wrapped(route):
        pg.__requests__.append((route.request.method, route.request.url))
        handler(route)

    pg.route(pattern, wrapped)


def _json_route(status, body):
    return lambda route: route.fulfill(
        status=status, content_type="application/json", body=json.dumps(body)
    )


# ---------------------------------------------------------------------------
# DEFECT 1 — "Same place" upload silently demoted an existing POI's tier
# ---------------------------------------------------------------------------


def test_defect1_same_place_upload_preserves_existing_importance_tier(page):
    """A tier-5 landmark must not be demoted to tier 1 by a re-upload.

    POST /nodes/POI MERGEs on (name_key, city_name) and then SETs every supplied
    property, so the old hardcoded ``importance_tier: 1`` rewrote a curated
    tier-5 landmark as tier 1. selection.py makes tour gravity LINEAR in tier,
    so the landmark's pull dropped 5x and it could fall out of built tours.
    """
    payload = page.evaluate(
        """() => window.mapPoiForApi(
             { poi_name: 'Notre-Dame', latitude: 48.85, longitude: 2.35 },
             { useExistingName: 'Notre-Dame', existingImportanceTier: 5 },
           )"""
    )
    assert payload["importance_tier"] == 5, (
        "the existing node's curated importance_tier must be carried through the "
        "MERGE, not overwritten with the hardcoded default of 1"
    )


def test_defect1_absent_description_never_blanks_the_curated_one(page):
    """An incoming record with no description must not blank the existing one.

    The MERGE SETs whatever is supplied and POICreate defaults
    ``short_description`` to '', so simply omitting the key is NOT sufficient —
    pydantic re-supplies '' and the SET blanks the curated text. The existing
    value is echoed back instead, so the SET rewrites the same string.
    """
    payload = page.evaluate(
        """() => window.mapPoiForApi(
             { poi_name: 'Notre-Dame', latitude: 48.85, longitude: 2.35,
               short_description: '   ' },
             { useExistingName: 'Notre-Dame', existingImportanceTier: 5,
               existingShortDescription: 'The cathedral on the Ile de la Cite.' },
           )"""
    )
    assert payload.get("short_description") == "The cathedral on the Ile de la Cite.", (
        "an empty incoming description must never overwrite the curated one; "
        f"got {payload.get('short_description')!r}"
    )


def test_defect1_no_description_anywhere_omits_the_key(page):
    """With nothing to write, the key is omitted rather than sent as ''."""
    payload = page.evaluate(
        """() => window.mapPoiForApi(
             { poi_name: 'Somewhere', latitude: 48.85, longitude: 2.35 }, {})"""
    )
    assert "short_description" not in payload, (
        f"expected the key to be omitted, got {payload.get('short_description')!r}"
    )


# ---------------------------------------------------------------------------
# DEFECT 2 — "Same place" replaced name_variations instead of appending
# ---------------------------------------------------------------------------


def test_defect2_same_place_appends_alias_and_keeps_existing_variations(page):
    """Resolving "same place" must ADD the incoming name, never wipe the list.

    The flow exists to add an alias; it used to SET
    ``name_variations = [incomingName]``, destroying every curated alternative
    name. Lost aliases are unrecoverable from the graph and break alt-name
    duplicate detection on later runs.
    """
    posted = page.evaluate(
        """async () => {
        const bodies = [];
        window.cachedPoiList = [];
        window.lensSlugToId = {}; window.lensSlugSet = new Set();
        window.lensDisplayToSlug = {};
        const realFetch = window.fetch;
        window.fetch = async (url, opts) => {
          if (String(url).includes('/nodes/POI') && opts && opts.method === 'POST') {
            bodies.push(JSON.parse(opts.body));
            return new Response(JSON.stringify({ id: 'p1', properties: {} }),
              { status: 201, headers: { 'Content-Type': 'application/json' } });
          }
          return realFetch(url, opts);
        };
        window.poiData = [{
          poi_name: 'Notre Dame de Paris', latitude: 48.85, longitude: 2.35,
          beats: [],
          _conflicts: {
            proximityResolution: 'same',
            matchedExistingPoi: { id: 'db-1', properties: {
              name: 'Notre-Dame', importance_tier: 5,
              name_variations: ['Cathedrale Notre-Dame', 'ND'],
            } },
          },
        }];
        await window.uploadSinglePoi(0);
        window.fetch = realFetch;
        return bodies;
      }"""
    )
    assert len(posted) == 1, f"expected exactly one POI POST, got {len(posted)}"
    variations = [v.lower() for v in posted[0]["name_variations"]]
    assert "cathedrale notre-dame" in variations, (
        "the existing node's curated aliases must survive the 'same place' "
        f"upload; got {posted[0]['name_variations']!r}"
    )
    assert "nd" in variations, "every existing alias must survive, not just the first"
    assert "notre dame de paris" in variations, "the incoming name must still be ADDED"
    assert "notre-dame" not in variations, (
        "the node's own display name is n.name, not a variation"
    )


def test_defect2_merge_name_variations_dedupes_and_parses_encoded_lists(page):
    """Existing props may arrive JSON-encoded (serialize_neo4j_props)."""
    merged = page.evaluate(
        """() => window.mergeNameVariations(
             '["Cathedrale Notre-Dame","ND"]', ['nd', 'Notre Dame'], 'Notre-Dame')"""
    )
    lowered = [v.lower() for v in merged]
    assert lowered.count("nd") == 1, f"case-insensitive de-dupe failed: {merged!r}"
    assert "cathedrale notre-dame" in lowered, "JSON-encoded existing list must be parsed"


# ---------------------------------------------------------------------------
# DEFECT 3 — merge "Skip" orphaned the source beat
# ---------------------------------------------------------------------------


def test_defect3_skip_resolution_disposes_of_the_source_beat(page):
    """A skipped beat must be disposed of BEFORE the source POI is deleted.

    DELETE /nodes/POI DETACH DELETEs the source, removing the only HAS_BEAT edge
    to a skipped beat. The node then survived with active_status 'active' and no
    POI attachment — unreachable by every POI-scoped query, still counted by node
    totals and db_parity, and still picked up by the batch-TTS pass (which
    filters on audio_url, not active_status). The UI said "discard"; nothing was.
    """
    _stub(page, "**/api/v1/edges/HAS_BEAT**", _json_route(200, {"items": [], "total": 0}))
    page.evaluate(
        """async () => {
        await window.executeMerge(
          { id: 'target-1', properties: { name: 'Target' } },
          { id: 'source-1', properties: { name: 'Source' } },
          [{ sourceBeat: { id: 'beat-9', lens_slug: 'hidden_history' },
             collision: null, resolution: 'skip' }],
        );
      }"""
    )
    log = page.__requests__
    disposal = [
        i for i, (m, u) in enumerate(log)
        if "/nodes/NarrativeBeat/beat-9" in u and m in ("DELETE", "PUT")
    ]
    poi_delete = [
        i for i, (m, u) in enumerate(log) if m == "DELETE" and "/nodes/POI/source-1" in u
    ]
    assert disposal, (
        "a skipped source beat must be explicitly disposed of, not left to be "
        f"orphaned by the source-POI DETACH DELETE; requests were {log!r}"
    )
    assert poi_delete, "the merge should still delete the source POI"
    assert min(disposal) < min(poi_delete), (
        "the beat must be disposed of BEFORE the POI delete detaches it"
    )


# ---------------------------------------------------------------------------
# DEFECT 4 — merge preview read an API error as "source POI has no beats"
# ---------------------------------------------------------------------------


def test_defect4_beats_fetch_error_aborts_merge_preview_without_deleting(page):
    """A transient error on the beats fetch must never delete the source POI.

    The preview used ``.then(r => r.json())`` with no status check, so an error
    produced ``sourceBeats = []`` and the zero-beat branch told the editor "Source POI
    X has no beats. Merge will delete it." — about a POI that may hold dozens.
    On OK, executeMerge ran with an empty beat list and DELETEd the POI,
    orphaning every beat, then showed a green success toast.
    """
    page.on("dialog", lambda d: d.accept())  # worst case: the editor clicks OK
    _stub(page, "**/api/v1/graph/poi/**", _json_route(500, {"detail": "boom"}))
    page.evaluate(
        """async () => {
        await window.showMergePreview(
          { id: 'target-1', properties: { name: 'Target' } },
          { id: 'source-1', properties: { name: 'Source' } },
        );
      }"""
    )
    deletes = [(m, u) for m, u in page.__requests__ if m == "DELETE"]
    assert not deletes, (
        "a failed beats fetch must abort the preview — no POI or beat may be "
        f"deleted on the strength of an unread response; got {deletes!r}"
    )
    assert page.locator("#errorToast").is_visible(), "the failure must be surfaced"


def test_defect4_execute_merge_refuses_to_delete_a_poi_that_still_has_beats(page):
    """Emptiness is re-verified by NODE ID before the destructive DELETE.

    The beats endpoint keys on (name, city_name) — the coordinate that can
    silently mismatch. GET /edges/HAS_BEAT?source_id= checks the actual node, so
    the DELETE is unreachable whenever the POI really does hold beats.
    """
    _stub(
        page,
        "**/api/v1/edges/HAS_BEAT**",
        _json_route(
            200,
            {"items": [{"id": "e1", "source_id": "source-1", "target_id": "b1"}], "total": 1},
        ),
    )
    page.evaluate(
        """async () => {
        await window.executeMerge(
          { id: 'target-1', properties: { name: 'Target' } },
          { id: 'source-1', properties: { name: 'Source' } },
          [],
        );
      }"""
    )
    deletes = [(m, u) for m, u in page.__requests__ if m == "DELETE" and "/nodes/POI/" in u]
    assert not deletes, (
        "executeMerge must refuse to DETACH DELETE a source POI that still holds "
        f"HAS_BEAT edges; got {deletes!r}"
    )


# ---------------------------------------------------------------------------
# DEFECT 5 — renderDetail re-registered its delegated listeners every render
# ---------------------------------------------------------------------------


def test_defect5_repeated_renders_leave_exactly_one_live_click_listener(page):
    """Listener count must stay at 1 no matter how many times renderDetail runs.

    Each conflict-resolution click calls renderDetail() again, so the count
    doubled per click (1 -> 2 -> 4 -> 8 ...) and every handler did a full
    innerHTML rebuild — O(2^clicks), locking the tab after ~15-20 clicks. Before
    that, one click on a beat's "Listen" button fired ttsPlayBeat N times: N
    identical POST /audio/preview calls, i.e. N x real PAID OpenAI TTS.
    """
    live = page.evaluate(
        """async () => {
        window.poiCacheComplete = true;
        window.cachedPoiList = [];
        window.poiData = [{
          poi_name: 'Louvre', latitude: 48.86, longitude: 2.33,
          short_description: 'x', _status: 'pending',
          beats: [{ lens: 'hidden_history', script_body: 'body', gravity: 3 }],
        }];
        // selectPoi sets activeIdx + activeSelection, which is what makes
        // renderDetail take the incoming-POI branch that registers the
        // delegated listeners. Then re-render as a resolution click would.
        await window.selectPoi(0);
        const afterFirst = window.__liveDetailClickListeners();
        for (let i = 0; i < 5; i++) window.renderDetail();
        return { afterFirst, afterMany: window.__liveDetailClickListeners() };
      }"""
    )
    assert live["afterFirst"] >= 1, (
        "harness check: the render path under test must actually register a "
        "delegated #detailBody click listener, otherwise this test is vacuous"
    )
    assert live["afterMany"] == live["afterFirst"], (
        f"the live delegated #detailBody click listener count must be CONSTANT "
        f"across renders; it went {live['afterFirst']} -> {live['afterMany']} over 5 "
        f"extra renders (each extra listener re-runs the whole handler, including "
        f"the PAID /audio/preview call, and rebuilds detailBody.innerHTML)"
    )


def test_defect5_concurrent_tts_for_the_same_text_issues_one_paid_request(page):
    """The in-flight guard defends the paid path against duplicate invocations.

    The cache alone cannot deduplicate concurrent callers — they all pass the
    cache check before the first response lands. Without the guard, N callers
    issue N REAL paid TTS calls and leak N-1 blob URLs.
    """
    _stub(
        page,
        "**/api/v1/audio/preview",
        lambda route: route.fulfill(status=200, content_type="audio/mpeg", body="fake-audio"),
    )
    page.evaluate(
        """async () => {
        const btn = document.createElement('button');
        const audioEl = document.createElement('audio');
        audioEl.play = () => {};
        document.body.appendChild(btn);
        document.body.appendChild(audioEl);
        const args = { text: 'hello there', cacheKey: 'k1', btn, audioEl };
        await Promise.all([window.ttsPlay(args), window.ttsPlay(args), window.ttsPlay(args)]);
      }"""
    )
    tts_calls = [u for m, u in page.__requests__ if m == "POST" and "/audio/preview" in u]
    assert len(tts_calls) == 1, (
        f"three concurrent ttsPlay calls for the same text must cost ONE paid TTS "
        f"request, not {len(tts_calls)}"
    )


# ---------------------------------------------------------------------------
# DEFECT 6 — runBeatConflictDetection ignored the response status
# ---------------------------------------------------------------------------


def test_defect6_beats_api_error_reports_an_error_not_zero_conflicts(page):
    """An API error must surface, not read as "this POI has no existing beats".

    ``result.existingBeats = data.beats || []`` turned a 500 into [], so the
    hard-match and Jaccard loops iterated nothing and the function returned zero
    conflicts AND zero errors. The "resolve all beat conflicts" gate then passed
    VACUOUSLY and every incoming beat was POSTed as new — two active beats on the
    same POI and the same lens_slug, the exact collision this system prevents.
    """
    _stub(page, "**/api/v1/graph/poi/**", _json_route(500, {"detail": "boom"}))
    result = page.evaluate(
        """async () => {
        window.lensDisplayToSlug = { 'Hidden History': 'hidden_history' };
        window.lensSlugSet = new Set(['hidden_history']);
        const poi = { poi_name: 'Louvre',
          beats: [{ lens: 'hidden_history', script_body: 'body' }] };
        return await window.runBeatConflictDetection(poi, 'Louvre');
      }"""
    )
    assert result["errors"], (
        "a 500 from the beats endpoint must be recorded as an error, not "
        "silently reported as zero conflicts"
    )


def test_defect6_malformed_200_body_is_also_an_error(page):
    """A well-formed 200 with no ``beats`` key must fail loudly, not default."""
    _stub(page, "**/api/v1/graph/poi/**", _json_route(200, {"unexpected": "shape"}))
    result = page.evaluate(
        """async () => {
        window.lensDisplayToSlug = { 'Hidden History': 'hidden_history' };
        window.lensSlugSet = new Set(['hidden_history']);
        const poi = { poi_name: 'Louvre',
          beats: [{ lens: 'hidden_history', script_body: 'body' }] };
        return await window.runBeatConflictDetection(poi, 'Louvre');
      }"""
    )
    assert result["errors"], "a malformed 200 body must not masquerade as an empty beat list"


def test_defect6_conflict_detection_errors_block_mark_complete(page):
    """Detection errors must REFUSE the upload, not merely render."""
    page.evaluate(
        """async () => {
        // Stage a FULLY uploadable POI: valid lenses, valid beats, no
        // unresolved proximity match. The ONLY thing standing between it and a
        // write is the conflict-detection error — so if writes happen, it is
        // that gate that failed, not some unrelated validation.
        window.lensDisplayToSlug = { 'Hidden History': 'hidden_history' };
        window.lensSlugSet = new Set(['hidden_history']);
        window.lensSlugToId = { hidden_history: 'lens-1' };
        window.cachedPoiList = [];
        window.poiData = [{
          poi_name: 'Louvre', latitude: 48.86, longitude: 2.33, _status: 'pending',
          beats: [{ lens: 'hidden_history', script_body: 'b', gravity: 3 }],
        }];
        await window.selectPoi(0);
        window.poiData[0]._conflicts = {
          isNew: false, errors: ['Failed to fetch beats: HTTP 500'],
          proximityMatches: [], proximityResolution: null,
          beatConflicts: [], beatReviewItems: [],
        };
      }"""
    )
    # The city picker overlay is modal on a fresh load and would intercept the
    # click; the gate under test is the mark-complete handler, not the picker.
    page.evaluate(
        """() => {
        document.querySelector('#cityOverlay').style.display = 'none';
        document.querySelector('#markCompleteBtn').disabled = false;
      }"""
    )
    page.click("#markCompleteBtn")
    page.wait_for_timeout(200)
    posts = [u for m, u in page.__requests__ if m == "POST" and "/nodes/" in u]
    assert not posts, (
        "with conflict detection in an error state the upload must be refused; "
        f"instead these writes were issued: {posts!r}"
    )
    assert page.locator("#errorToast").is_visible(), "the refusal must be explained"


# ---------------------------------------------------------------------------
# DEFECT 7 — bulk conflict detection skipped the same check
# ---------------------------------------------------------------------------


def test_defect7_bulk_beats_fetch_failure_excludes_the_poi_from_the_plan(page):
    """A failed existing-beats fetch must exclude the POI, not plan every beat as new.

    ``poiEntry.existingBeats = []`` on error meant no conflict was generated and
    every incoming beat was pushed with ``action: 'create'`` — duplicating beats
    onto an existing POI while the summary reported "Upload Complete", zero
    errors. An unknown existing-beat set means detection DID NOT RUN.
    """
    _stub(page, "**/api/v1/graph/poi/**", _json_route(503, {"detail": "unavailable"}))
    plan = page.evaluate(
        """async () => {
        window.poiCacheComplete = true;
        window.lensDisplayToSlug = { 'Hidden History': 'hidden_history' };
        window.lensSlugSet = new Set(['hidden_history']);
        window.cachedPoiList = [{ id: 'db-1', properties: {
          name: 'Louvre', location: { lat: 48.8606, lng: 2.3376 } } }];
        const plan = await window.detectConflicts([{
          poi_name: 'Louvre', latitude: 48.8606, longitude: 2.3376,
          beats: [{ lens: 'hidden_history', script_body: 'body' }],
        }]);
        return {
          errors: plan.errors,
          matched: plan.matchedPois.length,
          newPois: plan.newPois.length,
        };
      }"""
    )
    assert plan["errors"], "the failed fetch must be reported, not swallowed"
    assert plan["matched"] == 0 and plan["newPois"] == 0, (
        "a POI whose existing beats could not be read must be EXCLUDED from the "
        f"upload plan so its beats are never silently created; got {plan!r}"
    )


# ---------------------------------------------------------------------------
# DEFECT 8 — POI cache pagination truncated silently
# ---------------------------------------------------------------------------


def test_defect8_truncated_poi_cache_fails_closed_and_blocks_auto_new(page):
    """A mid-page failure must disable duplicate detection, not shrink it silently.

    findProximityMatches is the SOLE guard against creating duplicate POIs, so a
    truncated cache made every POI in the missing pages read as "no proximity
    match" — and detectConflicts routed it to newPois, minting a duplicate node
    at the same coordinates.
    """
    page_size = 200
    first_page = {
        "items": [
            {
                "id": f"p{i}",
                "properties": {"name": f"POI {i}", "location": {"lat": 48.86, "lng": 2.33}},
            }
            for i in range(page_size)
        ],
        "total": 400,
    }

    def poi_route(route):
        if "skip=0" in route.request.url:
            _json_route(200, first_page)(route)
        else:
            _json_route(500, {"detail": "boom"})(route)

    _stub(page, "**/api/v1/nodes/POI**", poi_route)
    state = page.evaluate(
        """async () => {
        try { await window.fetchLensesAndPoiList(); } catch (e) { /* handled inside */ }
        return { complete: window.poiCacheComplete, cached: window.cachedPoiList.length };
      }"""
    )
    assert state["complete"] is False, (
        "a page failure must leave poiCacheComplete FALSE — a truncated corpus is "
        "not a corpus"
    )
    assert state["cached"] == 0, "the cache must be cleared, not left half-populated"

    result = page.evaluate(
        """async () => await window.detectConflictsForPoi(
             { poi_name: 'Somewhere New', latitude: 48.87, longitude: 2.34, beats: [] })"""
    )
    assert result["errors"], (
        "with an incomplete cache, 'no proximity match' is indistinguishable from "
        "'we never looked' — it must not be reported as a clean new POI"
    )


# ---------------------------------------------------------------------------
# DEFECT 9 — lens validation happened mid-loop, after earlier writes committed
# ---------------------------------------------------------------------------


def test_defect9_bad_lens_on_a_later_beat_causes_zero_writes(page):
    """A bad lens on beat 3 must fail BEFORE the first POST, not after two.

    POST /nodes/NarrativeBeat CREATEs (beat ids are deliberately
    server-generated, never client-supplied), so a partial write followed by the
    invited retry duplicates every beat already committed. Validating all lenses
    up front makes the whole class unreachable for this failure mode.
    """
    outcome = page.evaluate(
        """async () => {
        window.lensDisplayToSlug = { 'Hidden History': 'hidden_history' };
        window.lensSlugSet = new Set(['hidden_history']);
        window.lensSlugToId = { hidden_history: 'lens-1' };
        window.cachedPoiList = [];
        window.poiData = [{
          poi_name: 'Louvre', latitude: 48.86, longitude: 2.33,
          _conflicts: { isNew: true, errors: [] },
          beats: [
            { lens: 'hidden_history', script_body: 'one', gravity: 3 },
            { lens: 'hidden_history', script_body: 'two', gravity: 3 },
            { lens: 'no_such_lens', script_body: 'three', gravity: 3 },
          ],
        }];
        try { await window.uploadSinglePoi(0); return { threw: false }; }
        catch (e) { return { threw: true, message: e.message }; }
      }"""
    )
    assert outcome["threw"], "an unrecognized lens must still abort the upload"
    writes = [u for m, u in page.__requests__ if m == "POST" and "/nodes/" in u]
    assert not writes, (
        "the bad lens on beat 3 must be caught by the pre-flight pass, before any "
        f"node is written; instead these writes committed first: {writes!r}"
    )
