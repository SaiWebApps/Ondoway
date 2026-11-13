"""Regression tests for the audio router's spend/mutate hardening.

Each test here is red-first against a specific defect:

1. The paid/mutating audio routes (generate-batch, generate/{beat_id}, compare,
   compare/download, eval) were mounted UNCONDITIONALLY and unauthenticated on
   the public deployment. generate-batch alone re-voices and overwrites EVERY
   NarrativeBeat in the connected graph through a paid provider.
2. /audio/compare/download/{filename} 500'd (leaking the server temp path) on a
   URL-encoded '..' or '.' because Path(...).name does not strip them and
   exists() passes for a directory.
3. /audio/files/{key:path} 500'd (leaking the absolute storage path) on an empty
   key or a directory key, for the same exists()-vs-is_file() reason.
4. /audio/preview had no cache, so a replayed identical payload was billed again.
   (The rate limit this file used to guard was DELETED on 2026-07-31 by owner
   order, along with its tests; the text cap was DELETED on 2026-08-04 by owner
   order so the workbench judges the whole narration, and the test that pinned it
   was replaced by the one that pins the opposite.)
5. /audio/compare's on-disk cache was bounded only by AGE, so a caller could
   fill the container's ephemeral disk within the retention window.

TOMBSTONE (the per-beat audio library is deleted): the rows that pinned the
per-beat routes —
``test_generate_batch_is_not_reachable_on_prod_profile``, the ``generate-batch``
and ``generate/{beat_id}`` rows of ``test_spend_routes_404_on_prod_profile``, and
the whole "silently-dropped stop" section (``TestGenerateTripReportsOrphanPrimary``
over ``POST /audio/generate-trip``) — were DELETED, not adapted. The gate's
invariant (the paid admin surface is closed on the public deployment) still
holds for compare / compare-download / eval below.
"""

from __future__ import annotations

import pathlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.routes import audio as audio_routes


@pytest.fixture(autouse=True)
def _temp_audio_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIO_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("AUDIO_STORAGE", "local")




def _client() -> TestClient:
    with patch("src.api.app.init_driver"), patch("src.api.app.close_driver"):
        app = create_app()
        return TestClient(app)


@pytest.fixture()
def prod_client(monkeypatch):
    """A prod-shaped app: the workbench/admin gate is explicitly OFF."""
    monkeypatch.setenv("WORKBENCH_API_ENABLED", "false")
    with _client() as c:
        yield c


@pytest.fixture()
def admin_client(monkeypatch):
    """A workbench-shaped app: the admin gate is explicitly ON."""
    monkeypatch.setenv("WORKBENCH_API_ENABLED", "true")
    with _client() as c:
        yield c


# ── The admin gate ────────────────────────────────────────────────────────


class TestAdminGate:
    """Unauthenticated paid-TTS routes must not be reachable on prod."""

    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            ("post", "/api/v1/audio/compare", {"text": "hi", "providers": ["openai"]}),
            ("post", "/api/v1/audio/eval", {"text": "hi", "provider": "openai"}),
        ],
    )
    def test_spend_routes_404_on_prod_profile(self, prod_client, method, path, payload):
        resp = getattr(prod_client, method)(path, json=payload)
        assert resp.status_code == 404, f"{path} -> {resp.status_code}: {resp.text}"

    def test_compare_download_404s_on_prod_profile(self, prod_client):
        resp = prod_client.get("/api/v1/audio/compare/download/anything.mp3")
        assert resp.status_code == 404

    def test_gate_is_fail_closed_on_unset_and_garbage(self, monkeypatch):
        """An unset or typo'd flag must keep the surface CLOSED, matching
        app._workbench_api_enabled's fail-closed contract.

        The probe used to be ``POST /audio/generate-batch``, a route that is now
        deleted — against a deleted route a 404 proves nothing about the gate, so the probe is now
        ``/audio/compare``, a paid route that stays admin-gated. The invariant
        is unchanged; with the gate ON the same request answers 200
        (``test_compare_is_reachable_when_admin_gate_is_on``).
        """
        probe = {"text": "hi", "providers": ["mock"]}
        monkeypatch.delenv("WORKBENCH_API_ENABLED", raising=False)
        with _client() as c:
            assert c.post("/api/v1/audio/compare", json=probe).status_code == 404
        for garbage in ("", "flase", "disbaled", "no", "0"):
            monkeypatch.setenv("WORKBENCH_API_ENABLED", garbage)
            with _client() as c:
                assert c.post("/api/v1/audio/compare", json=probe).status_code == 404, garbage

    def test_public_playback_routes_stay_mounted_on_prod_profile(self, prod_client):
        """The gate must NOT take the mobile/web surface down with it."""
        assert prod_client.get("/api/v1/audio/providers").status_code == 200
        # 404 from the handler's own "not found" path, not from an absent route.
        files = prod_client.get("/api/v1/audio/files/nope.mp3")
        assert files.status_code == 404
        preview = prod_client.post(
            "/api/v1/audio/preview", json={"text": "hello", "provider": "mock"}
        )
        assert preview.status_code == 200

    def test_render_manifest_actually_pins_the_gate_off(self):
        """The gate above is only worth anything if PROD really sets it off.
        Bind the two together so moving/renaming the render.yaml key cannot
        silently re-expose the whole-corpus TTS burn."""
        import yaml

        manifest = yaml.safe_load(pathlib.Path("render.yaml").read_text())
        api = next(
            s for s in manifest["services"] if s.get("name", "").startswith("ondoway-api")
        )
        env = {e["key"]: e.get("value") for e in api["envVars"] if "key" in e}
        assert audio_routes._ADMIN_GATE_ENV in env, (
            f"{audio_routes._ADMIN_GATE_ENV} is not pinned in render.yaml — the "
            "audio spend/mutate surface would be governed by an unset env var"
        )
        assert not audio_routes._audio_admin_enabled_value(env[audio_routes._ADMIN_GATE_ENV]), (
            f"render.yaml sets {audio_routes._ADMIN_GATE_ENV}="
            f"{env[audio_routes._ADMIN_GATE_ENV]!r}, which OPENS the audio "
            "generate/compare/eval surface on the public deployment"
        )

    def test_compare_is_reachable_when_admin_gate_is_on(self, admin_client):
        """Undo-check for the gate: the workbench profile still works."""
        resp = admin_client.post(
            "/api/v1/audio/compare", json={"text": "hello", "providers": ["mock"]}
        )
        assert resp.status_code == 200


# ── 500-instead-of-404 leaks ──────────────────────────────────────────────


class TestPathHandling500s:
    @pytest.mark.parametrize("encoded", ["%2E%2E", "%2E"])
    def test_compare_download_traversal_returns_404_not_500(self, admin_client, encoded):
        """Path(..).name does not strip '..' or '.', and exists() passes for a
        DIRECTORY, so FileResponse raised RuntimeError -> 500 with the server's
        temp path in it. Must be a clean 404."""
        resp = admin_client.get(f"/api/v1/audio/compare/download/{encoded}")
        assert resp.status_code == 404, resp.text

    def test_audio_files_empty_key_returns_404_not_500(self, prod_client):
        """An empty key resolves to the storage ROOT, which exists() accepted."""
        resp = prod_client.get("/api/v1/audio/files/")
        assert resp.status_code == 404, resp.text

    def test_audio_files_directory_key_returns_404_not_500(self, prod_client, tmp_path):
        (tmp_path / "subdir_probe").mkdir()
        resp = prod_client.get("/api/v1/audio/files/subdir_probe")
        assert resp.status_code == 404, resp.text

    def test_audio_files_still_serves_a_real_file(self, prod_client, tmp_path):
        (tmp_path / "real.mp3").write_bytes(b"ID3audio")
        resp = prod_client.get("/api/v1/audio/files/real.mp3")
        assert resp.status_code == 200
        assert resp.content == b"ID3audio"


# ── Anonymous paid-TTS spend bounds ───────────────────────────────────────


class TestPreviewSpendBounds:

    def test_preview_voices_the_whole_narration_uncapped(self, prod_client):
        """The preview must voice every character it is given.

        The old cap silently truncated, so the workbench judged a fragment while
        believing it judged the tour. It was an abuse bound on an anonymous
        endpoint, not a quality setting, and it was deleted 2026-08-04 by owner
        order. Equality, not a length bound: a `<=` would pass on any cap above
        the payload.
        """
        narration = ("word " * 4000)[:12000]
        assert len(narration) == 12000, "the payload itself must be long enough to be capped"

        seen: list[str] = []

        class _FakePaid:
            name = "openai"

            def generate(self, text, voice_id=None):
                seen.append(text)
                return b"mp3"

        with patch.object(audio_routes, "get_provider", return_value=_FakePaid()):
            resp = prod_client.post(
                "/api/v1/audio/preview",
                json={"text": narration, "provider": "openai"},
            )

        assert resp.status_code == 200, resp.text
        assert len(seen) == 1
        assert seen[0] == narration, (
            f"the provider was handed {len(seen[0])} of {len(narration)} characters — "
            "the preview truncated the narration"
        )

    def test_preview_caches_identical_payloads(self, prod_client):
        """A replayed identical payload must never be re-billed."""
        calls: list[str] = []

        class _FakePaid:
            name = "openai"

            def generate(self, text, voice_id=None):
                calls.append(text)
                return b"mp3-bytes"

        with patch.object(audio_routes, "get_provider", return_value=_FakePaid()):
            first = prod_client.post(
                "/api/v1/audio/preview", json={"text": "same text", "provider": "openai"}
            )
            second = prod_client.post(
                "/api/v1/audio/preview", json={"text": "same text", "provider": "openai"}
            )

        assert first.status_code == second.status_code == 200
        assert first.content == second.content == b"mp3-bytes"
        assert len(calls) == 1, "identical payload must hit the cache, not the paid provider"

    def test_preview_cache_discriminates_on_voice(self, prod_client):
        calls: list[tuple[str, str | None]] = []

        class _FakePaid:
            name = "openai"

            def generate(self, text, voice_id=None):
                calls.append((text, voice_id))
                return f"audio-{voice_id}".encode()

        with patch.object(audio_routes, "get_provider", return_value=_FakePaid()):
            a = prod_client.post(
                "/api/v1/audio/preview",
                json={"text": "same", "provider": "openai", "voice_id": "nova"},
            )
            b = prod_client.post(
                "/api/v1/audio/preview",
                json={"text": "same", "provider": "openai", "voice_id": "echo"},
            )

        assert a.content == b"audio-nova"
        assert b.content == b"audio-echo"
        assert len(calls) == 2


class TestCompareDiskBounds:
    def test_compare_cache_is_bounded_by_file_count(self, admin_client, monkeypatch):
        """Age-only cleanup bounded nothing inside the retention window."""
        monkeypatch.setattr(audio_routes, "_COMPARE_MAX_FILES", 3)
        monkeypatch.setattr(audio_routes, "_COMPARE_MAX_TOTAL_BYTES", 10**9)

        for i in range(10):
            resp = admin_client.post(
                "/api/v1/audio/compare", json={"text": f"text {i}", "providers": ["mock"]}
            )
            assert resp.status_code == 200

        files = [p for p in audio_routes._COMPARE_DIR.iterdir() if p.is_file()]
        assert len(files) <= 3, f"{len(files)} files survived a cap of 3"

    def test_compare_cache_is_bounded_by_total_bytes(self, admin_client, monkeypatch):
        monkeypatch.setattr(audio_routes, "_COMPARE_MAX_FILES", 10**6)
        monkeypatch.setattr(audio_routes, "_COMPARE_MAX_TOTAL_BYTES", 5000)

        for i in range(10):
            resp = admin_client.post(
                "/api/v1/audio/compare", json={"text": f"text {i}", "providers": ["mock"]}
            )
            assert resp.status_code == 200

        total = sum(p.stat().st_size for p in audio_routes._COMPARE_DIR.iterdir() if p.is_file())
        assert total <= 5000, f"{total} bytes survived a cap of 5000"

    def test_compare_dedupes_repeated_providers(self, admin_client):
        """max_length=5 with no uniqueness check let ["openai"]*5 multiply the
        bill 5x for one text."""
        calls: list[str] = []

        class _FakePaid:
            name = "openai"

            def generate(self, text, voice_id=None):
                calls.append(text)
                return b"mp3"

        with patch.object(audio_routes, "get_provider", return_value=_FakePaid()):
            resp = admin_client.post(
                "/api/v1/audio/compare",
                json={"text": "hello", "providers": ["openai"] * 5},
            )

        assert resp.status_code == 200
        assert len(calls) == 1, f"provider was billed {len(calls)} times for one text"
        assert len(resp.json()["results"]) == 1


# ── Ephemeral-storage self-heal ───────────────────────────────────────────


class TestArtifactMissing:
    """A persisted audio_url whose bytes are gone must be treated as no-audio so a
    non-force regeneration self-heals, instead of skipping forever after a redeploy."""

    def test_missing_local_artifact_is_detected(self, tmp_path):
        assert audio_routes._artifact_missing("/api/v1/audio/files/gone.mp3") is True

    def test_present_local_artifact_is_not_missing(self, tmp_path):
        (tmp_path / "here.mp3").write_bytes(b"x")
        assert audio_routes._artifact_missing("/api/v1/audio/files/here.mp3") is False

    @pytest.mark.parametrize(
        "url",
        [None, "", "https://cdn.example.com/audio/abc.mp3", "/api/v1/audio/files/"],
    )
    def test_unverifiable_urls_are_never_reported_missing(self, url):
        """Conservative by construction: an unverifiable pointer must never
        trigger a spurious (paid) regeneration."""
        assert audio_routes._artifact_missing(url) is False


# ── The silently-dropped stop — TOMBSTONE ────────────────────────────────
# ``TestGenerateTripReportsOrphanPrimary`` (+ ``_seed_orphan_trip`` and the
# ORPHAN_* ids) pinned ``POST /audio/generate-trip``'s per-beat orphan report.
# That route belonged to the per-beat audio library, which is deleted; the
# per-stop path it is replaced by voices an item's own ``narration`` and has no
# primary-beat edge to orphan. Deleted, not adapted.
