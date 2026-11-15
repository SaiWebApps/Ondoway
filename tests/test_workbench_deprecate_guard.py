"""Regression tests for the beat-deprecation write guards in review.html.

These are RED-FIRST regressions for the "unchecked deprecate PUT" defect class:
the four upload paths that deprecate an existing active beat before creating its
replacement/merge (uploadSinglePoi, uploadIncomingBeatsForDbPoi, and the two
executeUpload conflict branches) must treat a failed deprecate PUT (`!resp.ok`)
as an error, NOT proceed to create a second active beat on the same lens.

Before the fix each `await fetch(...PUT...)` ignored `.ok`, so a 500 on the
deprecate left the OLD beat active AND created a NEW active beat on the same
lens (a same-lens collision) while the UI reported success. After the fix the
guard throws before the create, so:
  * uploadSinglePoi / uploadIncomingBeatsForDbPoi never issue the create POST,
  * executeUpload records the item in stats.errors and does NOT bump
    beatsReplaced / beatsMerged.

This suite is browser-only (Playwright + Chromium) but needs NO API or Neo4j:
`window.fetch` is fully stubbed in-page, so it is deterministic and cheap. It
skips cleanly when Chromium is unavailable, mirroring test_workbench_ui.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync.sync_playwright

REVIEW_HTML = (Path(__file__).parent.parent / "web" / "workbench" / "review.html").resolve()


from tests.browser_launch import chromium_launch_options, find_chromium

# A `window.fetch` stub installed BEFORE review.html runs. It records every call
# as {method, url} on window.__fetchCalls and lets the test decide the response
# per-request via window.__fetchPlan(method, url) -> {ok, status, body}. The
# default plan returns 200 OK for everything; individual tests override it to
# fail only the deprecate PUT.
_FETCH_STUB = r"""
window.__fetchCalls = [];
window.__fetchPlan = (method, url) => ({ ok: true, status: 200, body: {} });
window.fetch = async (url, opts) => {
  const method = (opts && opts.method) || 'GET';
  window.__fetchCalls.push({ method, url: String(url) });
  const plan = window.__fetchPlan(method, String(url));
  return {
    ok: plan.ok,
    status: plan.status,
    json: async () => (plan.body === undefined ? {} : plan.body),
  };
};
// Neutralize Leaflet so renderDetail's map init is a harmless no-op offline
// (the CDN <script> may not load under file://). The detail HTML — including
// the lat/lng inputs under test — is written to the DOM before initMap runs.
const __noop = new Proxy(function(){}, {
  get: () => __noopFn,
  apply: () => __noopObj,
});
const __noopObj = new Proxy({}, { get: () => __noopFn });
function __noopFn() { return __noopObj; }
window.L = new Proxy({}, { get: () => __noopFn });
"""


@pytest.fixture()
def page():
    if find_chromium() is None:
        pytest.skip("Playwright Chromium not installed")
    with sync_playwright() as p:
        browser = p.chromium.launch(**chromium_launch_options())
        pg = browser.new_page()
        # Install the fetch stub before any page script executes so the workbench
        # module (an IIFE that captures `fetch`) uses our stub, not the real one.
        pg.add_init_script(_FETCH_STUB)
        pg.goto(REVIEW_HTML.as_uri())
        # Wait until the module's test-exposure block has run.
        pg.wait_for_function("() => typeof window.uploadSinglePoi === 'function'")
        yield pg
        browser.close()


# Lens maps staged so mapBeatForApi/resolveLensSlug resolve without a live fetch.
_STAGE_LENS = """
window.lensSlugToId = { hidden_history: 'lens-hh-id' };
window.lensSlugSet = new Set(['hidden_history']);
window.lensDisplayToSlug = { 'Hidden History': 'hidden_history' };
"""

# Fail ONLY the NarrativeBeat deprecate PUT; everything else succeeds. An id-less
# node URL with method PUT is the deprecate call.
_FAIL_DEPRECATE_PLAN = """
window.__fetchPlan = (method, url) => {
  if (method === 'PUT' && /\\/nodes\\/NarrativeBeat\\//.test(url)) {
    return { ok: false, status: 500, body: {} };
  }
  return { ok: true, status: 200, body: { id: 'stub-id', version: 1 } };
};
"""


def _created_beat_posts(page) -> int:
    """Count NarrativeBeat *create* POSTs recorded by the fetch stub."""
    return page.evaluate(
        "() => window.__fetchCalls.filter(c => "
        "c.method === 'POST' && /\\/nodes\\/NarrativeBeat$/.test(c.url)).length"
    )


class TestDeprecateGuardSinglePoi:
    """uploadSinglePoi (single-POI Replace path)."""

    def test_failed_deprecate_aborts_before_create(self, page):
        page.evaluate(_STAGE_LENS)
        page.evaluate(_FAIL_DEPRECATE_PLAN)
        result = page.evaluate(
            """async () => {
              window.poiData = [{
                poi_name: 'Test POI', latitude: 48.85, longitude: 2.35,
                beats: [{
                  lens: 'hidden_history', gravity: 3, script_body: 'New body.',
                  _conflictResolution: {
                    action: 'replace',
                    existingBeat: { id: 'old-beat-1', version: 1 },
                  },
                }],
              }];
              try {
                await window.uploadSinglePoi(0);
                return { threw: false };
              } catch (e) {
                return { threw: true, message: e.message };
              }
            }"""
        )
        # RED without fix: the deprecate PUT is ignored, uploadSinglePoi returns
        # normally (threw:false) AND a create POST fires -> two active beats.
        assert result["threw"] is True, (
            "uploadSinglePoi must throw when the deprecate PUT fails, "
            f"got a silent success: {result}"
        )
        assert "deprecate" in result["message"].lower()
        assert _created_beat_posts(page) == 0, (
            "A replacement beat was CREATED even though deprecation failed — "
            "two active beats now collide on the same lens"
        )


class TestDeprecateGuardIncomingBeats:
    """uploadIncomingBeatsForDbPoi (matched-DB-POI Replace path)."""

    def test_failed_deprecate_aborts_before_create(self, page):
        page.evaluate(_STAGE_LENS)
        page.evaluate(_FAIL_DEPRECATE_PLAN)
        # This function swallows the throw into showError, so we assert the
        # observable effect: no replacement beat was created, and the error
        # toast is shown instead of the success toast.
        page.evaluate(
            """async () => {
              window.cachedPoiList = [{
                id: 'db-poi-1',
                properties: { name: 'DB POI', location: { lat: 48.85, lng: 2.35 } },
                _incomingBeats: [{
                  lens: 'hidden_history', gravity: 3, script_body: 'Incoming body.',
                  _conflictResolution: {
                    action: 'replace',
                    existingBeat: { id: 'old-beat-2', version: 1 },
                  },
                }],
              }];
              await window.uploadIncomingBeatsForDbPoi(0);
            }"""
        )
        assert _created_beat_posts(page) == 0, (
            "A replacement beat was CREATED even though deprecation failed — "
            "the old beat stays active alongside the new one"
        )
        error_visible = page.evaluate(
            "() => document.getElementById('errorToast')"
            ".classList.contains('visible')"
        )
        success_visible = page.evaluate(
            "() => document.getElementById('successToast')"
            ".classList.contains('visible')"
        )
        assert error_visible and not success_visible, (
            "A failed deprecation must surface as an error, not a success toast"
        )


class TestDeprecateGuardExecuteUpload:
    """executeUpload replace & merge conflict branches."""

    def _run_conflict(self, page, resolution: str) -> dict:
        page.evaluate(_STAGE_LENS)
        page.evaluate(_FAIL_DEPRECATE_PLAN)
        return page.evaluate(
            """async ([resolution]) => {
              const poi = { poi_name: 'Test POI', latitude: 48.85, longitude: 2.35 };
              const beat = {
                lens: 'hidden_history', gravity: 3, script_body: 'Beat body.',
              };
              const conflict = {
                resolution,
                resolved: true,
                poi,
                beat,
                existing: { id: 'old-beat-3', version: 1 },
                mergedBeat: { lens: 'hidden_history', gravity: 3, script_body: 'Merged.' },
              };
              const plan = {
                conflicts: [conflict], reviewItems: [],
                newPois: [], matchedPois: [], errors: [],
              };
              return await window.executeUpload(plan);
            }""",
            [resolution],
        )

    def test_replace_failed_deprecate_lands_in_errors(self, page):
        stats = self._run_conflict(page, "replace")
        # RED without fix: deprecate ignored -> beatsReplaced bumped and no error.
        assert stats["beatsReplaced"] == 0, (
            f"beatsReplaced must not count a failed-deprecate item: {stats}"
        )
        assert len(stats["errors"]) >= 1, (
            f"A failed deprecate PUT must be recorded in stats.errors: {stats}"
        )
        assert _created_beat_posts(page) == 0, (
            "A replacement beat was created despite a failed deprecation"
        )

    def test_merge_failed_deprecate_lands_in_errors(self, page):
        stats = self._run_conflict(page, "merge")
        assert stats["beatsMerged"] == 0, (
            f"beatsMerged must not count a failed-deprecate item: {stats}"
        )
        assert len(stats["errors"]) >= 1, (
            f"A failed deprecate PUT must be recorded in stats.errors: {stats}"
        )
        assert _created_beat_posts(page) == 0, (
            "A merged beat was created despite a failed deprecation"
        )


class TestLatLngAttributeEscaping:
    """Lat/lng interpolated into a value attribute must be escaped."""

    def test_non_numeric_coord_is_escaped_in_value_attribute(self, page):
        # Render the detail form for a POI whose latitude carries an attribute-
        # breakout payload (as a stored non-numeric string). Before the fix the
        # raw `"><img...>` breaks out of value="..." and injects a live element.
        page.evaluate(_STAGE_LENS)
        injected = page.evaluate(
            """async () => {
              const payload = '\"><img src=x onerror=\"window.__xss=1\">';
              window.poiData = [{
                poi_name: 'XSS POI',
                latitude: payload,
                longitude: 2.35,
                beats: [],
                // Pre-stage conflicts + status so selectPoi goes straight to
                // renderDetail (no proximity fetch, no upload).
                _status: 'pending',
                _conflicts: {
                  isNew: true, existingBeats: [], beatConflicts: [],
                  beatReviewItems: [], errors: [], proximityMatches: [],
                  proximityResolution: null, matchedExistingPoi: null,
                  missingCoords: false,
                },
              }];
              try {
                await window.selectPoi(0);
              } catch (e) { /* map/geocode side effects are irrelevant here */ }
              // Count <img> elements injected inside the detail form. With the
              // fix there are none; the payload lives only as the input's value.
              const injectedImgs = document
                .getElementById('detailBody')
                .querySelectorAll('img[onerror]').length;
              const latInput = document.querySelector('[data-field=\"latitude\"]');
              return {
                injectedImgs,
                latOuterHtml: latInput ? latInput.outerHTML : null,
                latValueAttr: latInput ? latInput.getAttribute('value') : null,
              };
            }"""
        )
        assert injected["injectedImgs"] == 0, (
            "Unescaped latitude broke out of the value attribute and injected an "
            "<img> element (stored self-XSS sink)"
        )
        # The payload must be contained within the input's own value attribute,
        # escaped — the raw markup must NOT appear as live sibling content. A
        # <input type=number> normalizes .value to '' for a non-numeric string,
        # so we assert on the parsed value attribute, which holds the full
        # payload only when it was escaped into the attribute rather than
        # breaking out of it.
        assert injected["latValueAttr"] == '"><img src=x onerror="window.__xss=1">', (
            "Escaped latitude should be contained in the value attribute, got: "
            f"{injected['latValueAttr']!r} (outerHTML={injected['latOuterHtml']!r})"
        )
