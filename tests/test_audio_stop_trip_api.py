"""POST /audio/generate-trip-stops/{trip_id}: per-stop narration audio.

Integration tests through the real endpoint + real LocalStorageProvider (tmp dir).
A recording provider captures the text actually sent to TTS, proving each stop is
voiced from its stitched `narration` and the artifact is persisted on
the ItineraryItem, keyed by the stop id (not the beat).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.api.auth.tokens import create_access_token
from src.audio.provider import MockTTSProvider
from src.audio.storage import get_storage
from src.connection import get_database
from src.tour.recipe_store import link_stops_to_recipes, stop_recipe_pieces
from tests.conftest import needs_neo4j
from tests.test_place_recipes import _Audio, _build, _seed_graph, _Writer
from tests.test_recipe_reuse import _FRAME, _FRAME_SECONDS, _TAG, _tower_first

TRIP_ID = "stop-audio-test-trip"
#: The trip's captain — /audio/generate-trip-stops is reader-scoped (captain
#: or crew of the trip), so the voicing calls sign as this identity.
CAPTAIN_USER_ID = "stop-audio-test-user"
CAPTAIN_EMAIL = "stop-audio@example.test"
CAPTAIN_PROFILE_ID = "stop-audio-test-profile"


def _bearer() -> dict[str, str]:
    """Headers signing the request as the seeded captain, minted just now."""
    return {
        "Authorization": f"Bearer {create_access_token(CAPTAIN_USER_ID, CAPTAIN_EMAIL)}"
    }


N1 = "Settle in. Welcome to the Eiffel Tower."
N2 = "Now walk on to the Arc de Triomphe."
# item1 also carries a "keep exploring here" extra narration; item2 does not.
KE_EXTRA = "The tower has three visitor levels. The top is 276 metres up."
# The authored session lines the owner ruled PRE-VOICED in the
# narrator's own voice — each stop's close, its thread lines, the full telling's
# close. The set's fixed lines stay screen-only and are never voiced.
CLOSE1 = "And that's the tower — iron meant to stand twenty years."
THREAD1 = "The same iron age built the next hall."
FULL_CLOSE1 = "And that's the tower's second story."
CLOSE2 = "That's the arch — an emperor's promise kept late."
# The stop's LEG piece — the walking line
# into it — voiced as its own file through the same narrator voice, played on the leg.
LEG2 = "Walk north up the avenue for about twelve minutes."
# A marquee's CHAPTER — the story cut at
# a reviewed anchor — voiced as its own file, its url and length written back into the
# item's chapter list.
SEG1 = "The iron lattice was meant to come down after twenty years."
SEGMENTS1 = [
    {
        "label": "The lattice", "lat": 48.8584, "lng": 2.2945, "radius_m": 40.0,
        "indoor": False, "narration": SEG1,
    }
]


class _Recorder:
    """Records each text sent to TTS; returns a valid (silent) WAV via the mock."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    @property
    def name(self) -> str:
        return "mock"

    def generate(self, text: str, *, voice_id: str | None = None) -> bytes:
        self.texts.append(text)
        return MockTTSProvider().generate(text)


@pytest.fixture(autouse=True)
def _temp_audio_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIO_STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("AUDIO_STORAGE", "local")
    return tmp_path


def _seed(driver) -> None:
    with driver.session(database=get_database()) as s:
        s.run(
            "MATCH (t:Trip {id: $tid}) OPTIONAL MATCH (t)-[:HAS_STOP]->(i:ItineraryItem) "
            "DETACH DELETE t, i",
            tid=TRIP_ID,
        )
        # clean_driver is module-scoped (no per-test wipe); remove this trip's
        # POIs too so re-seeding in a second test doesn't hit the POI.id uniqueness
        # constraint.
        s.run(
            "MATCH (p:POI) WHERE p.id STARTS WITH $prefix DETACH DELETE p",
            prefix=f"{TRIP_ID}-poi",
        )
        s.run(
            """
            CREATE (t:Trip {id: $tid, name: 'Stop audio test', status: 'planning',
                            created_at: datetime()})
            CREATE (p1:POI {id: $tid + '-poi1', name: 'Eiffel Tower'})
            CREATE (p2:POI {id: $tid + '-poi2', name: 'Arc de Triomphe'})
            CREATE (i1:ItineraryItem {id: $tid + '-item1', sort_order: 1, narration: $n1,
                                      extra_narration: $ke,
                                      close_text: $close1,
                                      thread_lines: $threads1,
                                      full_close_text: $fullclose1})
            SET i1.segments_json = $segments1
            CREATE (i2:ItineraryItem {id: $tid + '-item2', sort_order: 2, narration: $n2,
                                      close_text: $close2, leg_narration: $leg2})
            CREATE (i3:ItineraryItem {id: $tid + '-item3', sort_order: 3})
            CREATE (t)-[:HAS_STOP]->(i1)
            CREATE (t)-[:HAS_STOP]->(i2)
            CREATE (t)-[:HAS_STOP]->(i3)
            CREATE (i1)-[:AT_POI]->(p1)
            CREATE (i2)-[:AT_POI]->(p2)
            """,
            tid=TRIP_ID,
            n1=N1,
            n2=N2,
            ke=KE_EXTRA,
            close1=CLOSE1,
            threads1='{"Eiffel Tower": "' + THREAD1 + '"}',
            fullclose1=FULL_CLOSE1,
            close2=CLOSE2,
            leg2=LEG2,
            segments1=json.dumps(SEGMENTS1),
        )
        # The captain identity the reader-scoped voicing door resolves.
        s.run(
            "MERGE (u:User {id: $uid}) SET u.email = $email "
            "MERGE (p:Profile {id: $pid}) "
            "ON CREATE SET p.display_name = 'Stop audio captain', "
            "              p.created_at = datetime() "
            "MERGE (u)-[:HAS_PROFILE]->(p) "
            "WITH p MATCH (t:Trip {id: $tid}) MERGE (p)-[:IS_CAPTAIN_OF]->(t)",
            uid=CAPTAIN_USER_ID,
            email=CAPTAIN_EMAIL,
            pid=CAPTAIN_PROFILE_ID,
            tid=TRIP_ID,
        )


def _item_audio(driver) -> dict[str, str | None]:
    with driver.session(database=get_database()) as s:
        records = s.run(
            "MATCH (t:Trip {id: $tid})-[:HAS_STOP]->(i:ItineraryItem) "
            "RETURN i.id AS id, i.audio_url AS audio_url",
            tid=TRIP_ID,
        )
        return {r["id"]: r["audio_url"] for r in records}


@needs_neo4j
class TestGenerateTripStopAudio:
    def test_generates_one_artifact_per_stop_from_narration(
        self, client, clean_driver, _temp_audio_storage
    ):
        _seed(clean_driver)
        recorder = _Recorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder):
            resp = client.post(
                f"/api/v1/audio/generate-trip-stops/{TRIP_ID}", json={"provider": "mock"},
                headers=_bearer(),
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Two stops have narration → generated; the third (no narration) → skipped.
        assert data["generated"] == 2
        assert data["skipped"] == 1
        assert data["failed"] == 0

        # The exact stitched narration is voiced — not a lone beat.
        # RE-DERIVED: the voicing pass now ALSO records each
        # stop's authored session lines (close, threads, the full telling's
        # close) in the same narrator voice — the owner's ruling. The narration
        # is still voiced exactly once per stop. RE-DERIVED again: the
        # stop's LEG piece (item2's walking line) is voiced as its own file too.
        # RE-DERIVED again: item1's chapter is voiced as its own file too.
        assert set(recorder.texts) == {N1, N2, CLOSE1, THREAD1, FULL_CLOSE1, CLOSE2, LEG2, SEG1}

        # Audio persisted on each item, keyed by the stop id (not the beat).
        urls = _item_audio(clean_driver)
        assert urls[f"{TRIP_ID}-item1"] and f"{TRIP_ID}-item1" in urls[f"{TRIP_ID}-item1"]
        assert urls[f"{TRIP_ID}-item2"] and f"{TRIP_ID}-item2" in urls[f"{TRIP_ID}-item2"]
        assert urls[f"{TRIP_ID}-item3"] is None
        # The leg piece's file and its measured length live on the item.
        with clean_driver.session(database=get_database()) as s:
            leg = s.run(
                "MATCH (i:ItineraryItem {id: $iid}) "
                "RETURN i.leg_audio_url AS u, i.leg_audio_duration_sec AS d",
                iid=f"{TRIP_ID}-item2",
            ).single()
        assert leg["u"] and f"{TRIP_ID}-item2-leg" in leg["u"], leg
        assert leg["d"] and leg["d"] > 0
        # The chapter's file and length are written back INTO the item's
        # chapter list (the thread-urls precedent), never beside it.
        with clean_driver.session(database=get_database()) as s:
            raw = s.run(
                "MATCH (i:ItineraryItem {id: $iid}) RETURN i.segments_json AS j",
                iid=f"{TRIP_ID}-item1",
            ).single()["j"]
        segments = json.loads(raw)
        assert [seg["label"] for seg in segments] == ["The lattice"]
        assert segments[0]["narration"] == SEG1
        assert segments[0]["audio_url"] and f"{TRIP_ID}-item1-seg-0" in segments[0]["audio_url"]
        assert segments[0]["audio_duration_sec"] and segments[0]["audio_duration_sec"] > 0

        # One artifact per generated stop on disk.
        files = list(_temp_audio_storage.rglob("*.mp3"))
        # RE-DERIVED: two narration pieces PLUS the authored
        # session lines (item1's close, thread and full-close; item2's close).
        # RE-DERIVED again: plus item2's leg piece, plus item1's chapter.
        assert len(files) == 8

    def test_second_call_skips_existing(self, client, clean_driver, _temp_audio_storage):
        _seed(clean_driver)
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=_Recorder()):
            first = client.post(
                f"/api/v1/audio/generate-trip-stops/{TRIP_ID}", json={"provider": "mock"},
                headers=_bearer(),
            )
            assert first.status_code == 200, first.text
            assert first.json()["generated"] == 2

            second = client.post(
                f"/api/v1/audio/generate-trip-stops/{TRIP_ID}", json={"provider": "mock"},
                headers=_bearer(),
            )
        assert second.status_code == 200, second.text
        data = second.json()
        assert data["generated"] == 0
        assert data["failed"] == 0
        assert data["skipped"] == 3  # 2 already have audio + 1 has no narration

    def test_edited_narration_invalidates_its_stop_audio(
        self, client, clean_driver, _temp_audio_storage
    ):
        """Rewriting a stop's narration must re-voice THAT stop and only it.

        The per-stop path was the one generation path with no content hash, so
        "has a url" was the whole staleness test: an edited stop kept playing the
        old recording forever. Phase 2 is that defect. Phase 3 is the pre-existing
        self-heal (a row whose bytes vanished off disk must still re-voice), which
        now shares an `and` chain with the new hash term and must survive it.
        """
        item1 = f"{TRIP_ID}-item1"
        item2 = f"{TRIP_ID}-item2"
        edited = "Settle in. Welcome to the Eiffel Tower, rewritten."

        def _stored_hash(stop_id: str) -> str | None:
            with clean_driver.session(database=get_database()) as s:
                rec = s.run(
                    "MATCH (i:ItineraryItem {id: $sid}) RETURN i.audio_script_hash AS h",
                    sid=stop_id,
                ).single()
            return rec["h"] if rec else None

        # ── Phase 1 — baseline: both narrated stops voiced, and hashed.
        _seed(clean_driver)
        recorder1 = _Recorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder1):
            first = client.post(
                f"/api/v1/audio/generate-trip-stops/{TRIP_ID}", json={"provider": "mock"},
                headers=_bearer(),
            )
        assert first.status_code == 200, first.text
        assert first.json()["generated"] == 2, first.text

        stored = _stored_hash(item1)
        assert isinstance(stored, str) and len(stored) == 64, (
            f"no content hash recorded for {item1} (got {stored!r}) — without one, "
            "an edited narration can never be detected as stale"
        )

        # ── Phase 2 — rewrite item1's narration only.
        with clean_driver.session(database=get_database()) as s:
            s.run(
                "MATCH (i:ItineraryItem {id: $sid}) SET i.narration = $n",
                sid=item1,
                n=edited,
            )
        recorder2 = _Recorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder2):
            second = client.post(
                f"/api/v1/audio/generate-trip-stops/{TRIP_ID}", json={"provider": "mock"},
                headers=_bearer(),
            )
        assert second.status_code == 200, second.text
        data = second.json()
        by_stop = {r["stop_id"]: r for r in data["results"]}

        assert data["generated"] == 1, f"only the edited stop may re-voice: {data}"
        assert by_stop[item1]["status"] == "generated", by_stop[item1]
        assert data["skipped"] == 2, f"the untouched stops must both skip: {data}"
        assert by_stop[item2]["status"] == "skipped", by_stop[item2]
        assert by_stop[item2]["reason"] == "already has audio", by_stop[item2]
        assert recorder2.texts == [edited], (
            f"the provider must be handed the rewritten text exactly once: {recorder2.texts}"
        )

        # ── Phase 3 — the self-heal still fires beside the new hash term.
        urls = _item_audio(clean_driver)
        artifact = _temp_audio_storage / urls[item2].removeprefix("/api/v1/audio/files/")
        assert artifact.exists(), f"phase-3 setup found no artifact at {artifact}"
        artifact.unlink()
        assert not artifact.exists()

        recorder3 = _Recorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder3):
            third = client.post(
                f"/api/v1/audio/generate-trip-stops/{TRIP_ID}", json={"provider": "mock"},
                headers=_bearer(),
            )
        assert third.status_code == 200, third.text
        third_by_stop = {r["stop_id"]: r for r in third.json()["results"]}
        assert third_by_stop[item2]["status"] == "generated", (
            "a stop whose stored bytes vanished must re-voice even though its "
            f"hash is current — the self-heal must survive: {third_by_stop[item2]}"
        )
        assert recorder3.texts == [N2], recorder3.texts

    def test_unknown_trip_404(self, client, clean_driver):
        _seed(clean_driver)  # the caller identity must exist; the trip must not
        resp = client.post(
            "/api/v1/audio/generate-trip-stops/no-such-trip", json={"provider": "mock"},
            headers=_bearer(),
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


@needs_neo4j
class TestStopAudioStatus:
    def test_status_false_before_and_true_after_generation(
        self, client, clean_driver, _temp_audio_storage
    ):
        _seed(clean_driver)
        item1 = f"{TRIP_ID}-item1"

        before = client.get(f"/api/v1/audio/stop-status/{item1}")
        assert before.status_code == 200, before.text
        assert before.json()["has_audio"] is False
        assert before.json()["audio_url"] is None

        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=_Recorder()):
            gen = client.post(
                f"/api/v1/audio/generate-trip-stops/{TRIP_ID}", json={"provider": "mock"},
                headers=_bearer(),
            )
        assert gen.status_code == 200, gen.text

        after = client.get(f"/api/v1/audio/stop-status/{item1}")
        assert after.status_code == 200, after.text
        data = after.json()
        assert data["has_audio"] is True
        assert data["audio_url"] and item1 in data["audio_url"]
        assert data["duration_sec"] and data["duration_sec"] > 0

    def test_unknown_stop_404(self, client):
        resp = client.get("/api/v1/audio/stop-status/no-such-stop")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


@needs_neo4j
class TestKeepExploringStopAudio:
    """POST /audio/stops/{stop_id}/keep-exploring — on-demand extra audio."""

    def test_voices_extra_narration_off_budget(
        self, client, clean_driver, _temp_audio_storage
    ):
        _seed(clean_driver)
        item1 = f"{TRIP_ID}-item1"
        recorder = _Recorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder):
            resp = client.post(
                f"/api/v1/audio/stops/{item1}/keep-exploring", json={"provider": "mock"}
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "generated"
        assert data["stop_id"] == item1
        # The persisted extra_narration was voiced — verbatim, not the tour narration.
        assert recorder.texts == [KE_EXTRA]
        assert data["audio_url"] and item1 in data["audio_url"]
        assert data["duration_sec"] and data["duration_sec"] > 0
        # Served OFF the tour budget: the item's scheduled tour audio is untouched.
        assert _item_audio(clean_driver)[item1] is None

    def test_stop_without_extras_is_409(self, client, clean_driver, _temp_audio_storage):
        _seed(clean_driver)
        item2 = f"{TRIP_ID}-item2"  # has narration but no extra_narration
        resp = client.post(
            f"/api/v1/audio/stops/{item2}/keep-exploring", json={"provider": "mock"}
        )
        assert resp.status_code == 409, resp.text
        assert "keep-exploring" in resp.json()["detail"].lower()

    def test_unknown_stop_is_404(self, client, clean_driver, _temp_audio_storage):
        _seed(clean_driver)
        resp = client.post(
            "/api/v1/audio/stops/no-such-stop/keep-exploring", json={"provider": "mock"}
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_tts_failure_returns_200_failed(self, client, clean_driver, _temp_audio_storage):
        """Per audio.py's contract, a TTS failure is a soft 200 status='failed',
        never a 500 — the client can retry the on-demand deep dive."""
        _seed(clean_driver)
        item1 = f"{TRIP_ID}-item1"
        from src.audio.provider import TTSError

        class _Boom:
            name = "mock"

            def generate(self, text, *, voice_id=None):
                raise TTSError("provider exploded")

        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=_Boom()):
            resp = client.post(
                f"/api/v1/audio/stops/{item1}/keep-exploring", json={"provider": "mock"}
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "failed"
        assert data["audio_url"] is None
        assert data["error"]

    def test_second_call_returns_cached_url_no_retts(
        self, client, clean_driver, _temp_audio_storage
    ):
        """A repeat keep-exploring for the same stop must return the
        cached url WITHOUT re-invoking paid TTS (cost / DoS guard). The mock
        provider's call count proves the second call never voices again."""
        _seed(clean_driver)
        item1 = f"{TRIP_ID}-item1"
        recorder = _Recorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder):
            first = client.post(
                f"/api/v1/audio/stops/{item1}/keep-exploring", json={"provider": "mock"}
            )
            assert first.status_code == 200, first.text
            first_data = first.json()
            assert first_data["status"] == "generated"
            assert len(recorder.texts) == 1  # TTS ran exactly once

            second = client.post(
                f"/api/v1/audio/stops/{item1}/keep-exploring", json={"provider": "mock"}
            )
        assert second.status_code == 200, second.text
        second_data = second.json()
        # Same url returned, but TTS was NOT invoked a second time.
        assert second_data["audio_url"] == first_data["audio_url"]
        assert second_data["duration_sec"] == first_data["duration_sec"]
        assert len(recorder.texts) == 1, "cached hit must not re-run TTS"

    def test_different_voice_invalidates_cache(
        self, client, clean_driver, _temp_audio_storage
    ):
        """A keep-exploring TTS cache keyed on the narration hash ONLY would
        serve the wrong voice. A second call for the SAME narration but a
        DIFFERENT voice_id must NOT hit the cache — otherwise the client
        silently receives audio voiced with the ORIGINAL voice. The mock
        provider's call count proves the second (different-voice) call re-runs
        TTS instead of serving the cached url."""
        _seed(clean_driver)
        item1 = f"{TRIP_ID}-item1"
        recorder = _Recorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder):
            first = client.post(
                f"/api/v1/audio/stops/{item1}/keep-exploring",
                json={"provider": "mock", "voice_id": "alice"},
            )
            assert first.status_code == 200, first.text
            assert first.json()["status"] == "generated"
            assert len(recorder.texts) == 1  # TTS ran exactly once

            # Same narration, DIFFERENT voice — must not be served from cache.
            second = client.post(
                f"/api/v1/audio/stops/{item1}/keep-exploring",
                json={"provider": "mock", "voice_id": "bob"},
            )
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "generated"
        assert len(recorder.texts) == 2, "a different voice_id must invalidate the cache"

    def test_force_regenerates_even_when_cached(
        self, client, clean_driver, _temp_audio_storage
    ):
        """force=True bypasses the cache and re-voices."""
        _seed(clean_driver)
        item1 = f"{TRIP_ID}-item1"
        recorder = _Recorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder):
            first = client.post(
                f"/api/v1/audio/stops/{item1}/keep-exploring", json={"provider": "mock"}
            )
            assert first.status_code == 200, first.text
            assert len(recorder.texts) == 1

            forced = client.post(
                f"/api/v1/audio/stops/{item1}/keep-exploring",
                json={"provider": "mock", "force": True},
            )
        assert forced.status_code == 200, forced.text
        assert forced.json()["status"] == "generated"
        assert len(recorder.texts) == 2, "force=True must re-run TTS"

    def test_changed_extra_narration_invalidates_cache(
        self, client, clean_driver, _temp_audio_storage
    ):
        """If extra_narration changes, the cached url is stale and
        must be regenerated (hash-based invalidation), not served."""
        _seed(clean_driver)
        item1 = f"{TRIP_ID}-item1"
        recorder = _Recorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder):
            first = client.post(
                f"/api/v1/audio/stops/{item1}/keep-exploring", json={"provider": "mock"}
            )
            assert first.status_code == 200, first.text
            assert len(recorder.texts) == 1

            # Mutate the stop's extra_narration underneath the cache.
            with clean_driver.session(database=get_database()) as s:
                s.run(
                    "MATCH (i:ItineraryItem {id: $sid}) SET i.extra_narration = $new",
                    sid=item1,
                    new="A completely different deep dive about the ironwork.",
                )
            second = client.post(
                f"/api/v1/audio/stops/{item1}/keep-exploring", json={"provider": "mock"}
            )
        assert second.status_code == 200, second.text
        assert second.json()["status"] == "generated"
        assert len(recorder.texts) == 2, "changed narration must invalidate cache"

    def test_over_cap_extra_narration_is_bounded(
        self, client, clean_driver, _temp_audio_storage
    ):
        """An over-cap extra_narration must not fan out into unbounded
        TTS. The input handed to TTS is capped at the same 20000-char limit the
        other audio endpoints enforce (AudioPreviewRequest)."""
        _seed(clean_driver)
        item1 = f"{TRIP_ID}-item1"
        # 30k chars of full sentences — well past the 20000 cap.
        huge = ("The tower rises above Paris. " * 1100).strip()
        assert len(huge) > 20000
        with clean_driver.session(database=get_database()) as s:
            s.run(
                "MATCH (i:ItineraryItem {id: $sid}) SET i.extra_narration = $new",
                sid=item1,
                new=huge,
            )
        recorder = _Recorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder):
            resp = client.post(
                f"/api/v1/audio/stops/{item1}/keep-exploring", json={"provider": "mock"}
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "generated"
        # The text handed to TTS is capped: never longer than the input we sent,
        # and no longer than the enforced 20000-char limit.
        assert recorder.texts, "TTS should still run on capped input"
        voiced = recorder.texts[0]
        assert len(voiced) <= 20000, f"extra_narration must be capped, got {len(voiced)}"


@needs_neo4j
class TestUnknownProviderNever500:
    """An unknown provider name must be a soft per-stop
    failure, never an uncaught ValueError → 500. Each per-stop route that
    catches only PipelineError is exercised with {"provider": "evil"} against the
    REAL get_provider (no patch) so the unknown-provider ValueError actually
    fires at its true source (pipeline.get_provider_with_fallback).

    TOMBSTONE: the two per-BEAT rows — ``/audio/generate-trip`` and
    ``/audio/generate-batch`` (with ``_seed_beats``) — were DELETED, not adapted:
    those routes belong to the per-beat audio library, which is deleted.
    """

    def test_keep_exploring_unknown_provider_soft_fails(
        self, client, clean_driver, _temp_audio_storage
    ):
        _seed(clean_driver)
        item1 = f"{TRIP_ID}-item1"
        resp = client.post(
            f"/api/v1/audio/stops/{item1}/keep-exploring", json={"provider": "evil"}
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "failed"
        assert data["audio_url"] is None
        assert data["error"] and "evil" in data["error"].lower()

    def test_generate_trip_stops_unknown_provider_soft_fails(
        self, client, clean_driver, _temp_audio_storage
    ):
        _seed(clean_driver)
        resp = client.post(
            f"/api/v1/audio/generate-trip-stops/{TRIP_ID}", json={"provider": "evil"},
            headers=_bearer(),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Two stops carry narration; both fail (unknown provider), none succeed.
        assert data["generated"] == 0
        assert data["failed"] == 2
        for item in data["results"]:
            if item["status"] == "failed":
                assert "evil" in (item["error"] or "").lower()


@needs_neo4j
class TestPreVoicedSessionLines:
    """Owner decision, 2026-08-19: the tour's own voice, never the robot.
    The per-stop voicing pass ALSO records
    each stop's authored session lines — the close, the thread lines, the full
    telling's close — as their own small artifacts on the item, in the same
    narrator voice, hash-guarded like the narration. The contingency set's fixed
    lines ("Next: X") stay screen-only and are NEVER sent to TTS."""

    def test_the_authored_lines_are_voiced_persisted_and_hash_guarded(
        self, client, clean_driver, _temp_audio_storage
    ):
        _seed(clean_driver)
        recorder = _Recorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder):
            resp = client.post(
                f"/api/v1/audio/generate-trip-stops/{TRIP_ID}", json={"provider": "mock"},
                headers=_bearer(),
            )
        assert resp.status_code == 200
        # The narrator said the lines — and ONLY the authored lines.
        for line in (CLOSE1, THREAD1, FULL_CLOSE1, CLOSE2):
            assert line in recorder.texts, f"not voiced: {line!r}"
        assert not any("Next:" in t or "Straight to" in t for t in recorder.texts), (
            "the set's fixed lines are screen-only (R6, 8/11)"
        )
        with clean_driver.session(database=get_database()) as s:
            row = s.run(
                "MATCH (i:ItineraryItem {id: $iid}) "
                "RETURN i.close_audio_url AS c, i.thread_audio_urls AS t, "
                "       i.full_close_audio_url AS f",
                iid=f"{TRIP_ID}-item1",
            ).single()
        assert row["c"], "the close's file is on the item"
        assert row["f"], "the full telling's close has its file"
        import json as _json

        thread_urls = _json.loads(row["t"])
        assert set(thread_urls) == {"Eiffel Tower"} and thread_urls["Eiffel Tower"]

        # Voiced once: the second run skips every line (the hash guard).
        recorder2 = _Recorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder2):
            resp2 = client.post(
                f"/api/v1/audio/generate-trip-stops/{TRIP_ID}", json={"provider": "mock"},
                headers=_bearer(),
            )
        assert resp2.status_code == 200
        assert recorder2.texts == [], "everything voiced already — nothing re-billed"


@needs_neo4j
class TestSessionCarriesLiveAudio:
    """Audio facts live on the ITEMS; the saved session snapshots its
    stops at compose time, BEFORE voicing — so the session GET overlays the items'
    current audio fields at read time. Measured 2026-08-19: five voiced closes in
    the graph, none on the wire, until this overlay. UNDO: return the saved payload
    untouched -> RED."""

    def test_the_overlay_reads_the_items_current_files(self, client, clean_driver):
        from src.api.models.trips import GeneratedStop, SessionPlan
        from src.api.routes.trips import _with_live_audio

        _seed(clean_driver)
        item1 = f"{TRIP_ID}-item1"
        with clean_driver.session(database=get_database()) as s:
            s.run(
                "MATCH (i:ItineraryItem {id: $iid}) "
                "SET i.audio_url = 'file:///narr.mp3', i.audio_duration_sec = 40, "
                "    i.close_audio_url = 'file:///close.mp3', "
                "    i.thread_audio_urls = '{\"Eiffel Tower\": \"file:///thread.mp3\"}', "
                "    i.full_close_audio_url = 'file:///full-close.mp3', "
                "    i.leg_audio_url = 'file:///leg.mp3', i.leg_audio_duration_sec = 9, "
                "    i.segments_json = $segments",
                iid=item1,
                segments=json.dumps(
                    [
                        {
                            **SEGMENTS1[0],
                            "audio_url": "file:///seg.mp3",
                            "audio_duration_sec": 7.5,
                        }
                    ]
                ),
            )
            saved = SessionPlan(
                trip_id=TRIP_ID,
                plan_version=1,
                stops=[
                    GeneratedStop(
                        sort_order=1, stop_id=item1, poi_id="p", poi_name="Eiffel Tower",
                        lat=48.85, lng=2.29, duration_min=5, importance_tier=5,
                        start_time="10:00",
                    )
                ],
                retime_tolerance_seconds=180,
                day_start_hhmm="10:00",
            )
            live = _with_live_audio(s, saved)
        stop = live.stops[0]
        assert stop.audio_url == "file:///narr.mp3"
        assert stop.close_audio_url == "file:///close.mp3"
        assert stop.thread_audio_urls == {"Eiffel Tower": "file:///thread.mp3"}
        assert stop.full_close_audio_url == "file:///full-close.mp3"
        # The leg piece's file rides the same overlay.
        assert stop.leg_audio_url == "file:///leg.mp3"
        assert stop.leg_audio_duration_sec == 9
        # The chapters — with their files — ride the same overlay.
        assert [seg.label for seg in stop.segments] == ["The lattice"]
        assert stop.segments[0].audio_url == "file:///seg.mp3"
        assert stop.segments[0].audio_duration_sec == 7.5


@needs_neo4j
class TestTheFinishSentinelIsARealStop:
    """An A→B day's finish stop carried its goodbye as TEXT ONLY: no file could ever
    exist for it.
    Mechanism, as measured: `_create_itinerary_items`'s Cypher opens with
    `MATCH (poi:POI {id: $poi_id})`, the finish sentinel's id names no POI node, so the
    whole CREATE silently matched nothing — no item, no HAS_STOP — while the minted item
    id was returned and embedded in the saved session as a phantom. The voicing pass
    iterates HAS_STOP, so the finish could never be voiced. The CLASS is wider than the
    sentinel: ANY stop whose poi id is absent from the graph vanished silently instead
    of failing loudly (the guard below only counted beats).

    The fix: the POI match is OPTIONAL; the sentinel's item is CREATED (stored, voiced,
    its id real); a NON-sentinel stop whose POI is genuinely missing now raises."""

    TRIP2 = "finish-sentinel-test-trip"
    DAY_CLOSE = "That's the walk — a quai that still carries the old tanners' name."

    def _seed_trip(self, driver) -> None:
        with driver.session(database=get_database()) as s:
            s.run(
                "MATCH (t:Trip {id: $tid}) OPTIONAL MATCH (t)-[:HAS_STOP]->(i) "
                "DETACH DELETE t, i",
                tid=self.TRIP2,
            )
            s.run(
                "MATCH (p:POI {id: $pid}) DETACH DELETE p", pid=f"{self.TRIP2}-poi1"
            )
            s.run(
                "MERGE (pr:Profile {id: $prid}) "
                "CREATE (t:Trip {id: $tid, name: 'Finish test', status: 'planning', "
                "                created_at: datetime()}) "
                "CREATE (p1:POI {id: $pid, name: 'Palais de Justice'}) "
                # The reader-scoped voicing door: the module's caller identity
                # captains this trip through the trip's own profile.
                "MERGE (u:User {id: $uid}) SET u.email = $email "
                "MERGE (u)-[:HAS_PROFILE]->(pr) "
                "MERGE (pr)-[:IS_CAPTAIN_OF]->(t)",
                prid=f"{self.TRIP2}-profile",
                tid=self.TRIP2,
                pid=f"{self.TRIP2}-poi1",
                uid=CAPTAIN_USER_ID,
                email=CAPTAIN_EMAIL,
            )

    @staticmethod
    def _stop(poi_id: str, order: int, narration: str) -> dict:
        return {
            "poi_id": poi_id, "sort_order": order, "duration_min": 5,
            "start_time": f"16:0{order}", "beat_ids": [], "extra_beat_ids": [],
            "primary_beat_id": None, "lens_name": None, "narration": narration,
        }

    def test_the_sentinel_is_stored_and_voiced_and_a_wrong_poi_fails_loudly(
        self, client, clean_driver, _temp_audio_storage
    ):
        from src.api.crud.trips import _create_itinerary_items
        from src.tour.contract import END_B_SENTINEL_PREFIX

        self._seed_trip(clean_driver)
        stops = [
            self._stop(f"{self.TRIP2}-poi1", 1, "The last real stop's story."),
            self._stop(f"{END_B_SENTINEL_PREFIX}48.858_2.347", 2, self.DAY_CLOSE),
        ]
        with clean_driver.session(database=get_database()) as s:
            ids = s.execute_write(
                lambda tx: _create_itinerary_items(
                    tx, self.TRIP2, f"{self.TRIP2}-profile", stops
                )
            )
            rows = s.run(
                "MATCH (t:Trip {id: $tid})-[:HAS_STOP]->(i:ItineraryItem) "
                "RETURN i.id AS id, i.narration AS narr ORDER BY i.sort_order",
                tid=self.TRIP2,
            ).data()
        assert [r["id"] for r in rows] == ids, (
            "every returned item id must be a STORED item — a phantom id is the defect"
        )
        assert rows[1]["narr"] == self.DAY_CLOSE

        # The voicing pass now SEES the finish: its goodbye gets a file.
        recorder = _Recorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder):
            resp = client.post(
                f"/api/v1/audio/generate-trip-stops/{self.TRIP2}", json={"provider": "mock"},
                headers=_bearer(),
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["generated"] == 2, resp.text
        assert self.DAY_CLOSE in recorder.texts
        with clean_driver.session(database=get_database()) as s:
            url = s.run(
                "MATCH (i:ItineraryItem {id: $iid}) RETURN i.audio_url AS u",
                iid=ids[1],
            ).single()["u"]
        assert url, "the finish goodbye has its file at last"

        # And the CLASS guard: a NON-sentinel stop whose POI is missing fails loudly.
        self._seed_trip(clean_driver)
        with (
            clean_driver.session(database=get_database()) as s,
            pytest.raises(ValueError, match="no POI"),
        ):
            s.execute_write(
                lambda tx: _create_itinerary_items(
                    tx, self.TRIP2, f"{self.TRIP2}-profile",
                    [self._stop("no-such-poi-anywhere", 1, "text")],
                )
            )


# ── a stop told from a place recipe: its recorded pieces are reused ──

RECIPE_TRIP_ID = "recipe-audio-test-trip"
RECIPE_ITEM_ID = f"{RECIPE_TRIP_ID}-item1"
TOWER_RECIPE = "tower:no-lens:solo:outside"
#: The line the tour adds in front of the recipe's telling.
RECIPE_LEAD = "Settle in. You're standing at Test Tower."
#: Frames in each stored piece, and in each file the voice records fresh.
PIECE_FRAMES = 3
FRESH_FRAMES = 10


class _PieceVoice:
    """Records each recipe piece as a short MP3 file in the test's audio storage."""

    def __call__(self, text: str, *, stop_key: str, poi_name: str) -> _Audio:
        del text, poi_name
        url = get_storage("local").upload(_TAG + _FRAME * PIECE_FRAMES, f"pieces/{stop_key}.mp3")
        return _Audio(audio_url=url, duration_sec=round(PIECE_FRAMES * _FRAME_SECONDS, 2))


class _FramesRecorder:
    """Speaks any text as an MP3 file and records each text with the voice asked for."""

    name = "mock"

    def __init__(self) -> None:
        self.asked: list[tuple[str, str | None]] = []

    def generate(self, text: str, *, voice_id: str | None = None) -> bytes:
        self.asked.append((text, voice_id))
        return _TAG + _FRAME * FRESH_FRAMES


def _seed_recipe_stop(driver) -> dict[str, list]:
    """The tower's recipe built and voiced into storage, and a trip whose one stop was
    told from it: the tour's lead line, then the recipe's short telling, its close and
    its longer telling. Returns the recipe's stored pieces by kind."""
    snapshot = _tower_first()
    with driver.session(database=get_database()) as s:
        _seed_graph(s, snapshot)
        _build(s, snapshot, _Writer(), _PieceVoice())
        recipe_pieces = s.run(
            "MATCH (:PlaceRecipe {id: $id})-[h:HAS_PIECE]->(p) "
            "RETURN p.kind AS kind, p.text AS text ORDER BY h.order",
            id=TOWER_RECIPE,
        ).data()
        by_kind: dict[str, list[str]] = {}
        for row in recipe_pieces:
            by_kind.setdefault(row["kind"], []).append(row["text"])
        short = " ".join(
            text for kind in ("opening", "body", "close") for text in by_kind.get(kind, [])
        )
        s.run(
            """
            MERGE (u:User {id: $uid}) SET u.email = $email
            MERGE (p:Profile {id: $pid})
            MERGE (u)-[:HAS_PROFILE]->(p)
            CREATE (t:Trip {id: $tid, name: 'Recipe audio test', status: 'planning',
                            created_at: datetime()})
            CREATE (i:ItineraryItem {id: $iid, sort_order: 1, narration: $narration,
                                     close_text: $close, full_narration: $full,
                                     full_close_text: $full_close})
            MERGE (p)-[:IS_CAPTAIN_OF]->(t)
            CREATE (t)-[:HAS_STOP]->(i)
            WITH i MATCH (poi:POI {id: 'tower'}) CREATE (i)-[:AT_POI]->(poi)
            """,
            uid=CAPTAIN_USER_ID,
            email=CAPTAIN_EMAIL,
            pid=CAPTAIN_PROFILE_ID,
            tid=RECIPE_TRIP_ID,
            iid=RECIPE_ITEM_ID,
            narration=f"{RECIPE_LEAD} {short}",
            close=by_kind["close"][0],
            full=by_kind["full"][0],
            full_close=by_kind["full_close"][0],
        )
        link_stops_to_recipes(s, {RECIPE_ITEM_ID: TOWER_RECIPE})
        stored = stop_recipe_pieces(s, RECIPE_ITEM_ID)
    grouped: dict[str, list] = {}
    for piece in stored:
        grouped.setdefault(piece.kind, []).append(piece)
    return grouped


def _recipe_item(driver) -> dict:
    with driver.session(database=get_database()) as s:
        return s.run(
            "MATCH (i:ItineraryItem {id: $iid}) "
            "RETURN i.narration AS narration, i.audio_url AS audio_url, "
            "       i.audio_duration_sec AS duration, i.close_audio_url AS close_url, "
            "       i.full_close_audio_url AS full_close_url",
            iid=RECIPE_ITEM_ID,
        ).single().data()


@needs_neo4j
class TestRecipeStopAudio:
    """The voicing door and the keep-exploring door reuse a recipe-told stop's recorded
    pieces in the pinned voice, and voice everything fresh when a caller names a
    provider or a voice, because the pieces were recorded in the pinned one."""

    def test_trip_voicing_sends_only_the_new_line_and_reuses_the_recorded_pieces(
        self, client, clean_driver, _temp_audio_storage
    ):
        pieces = _seed_recipe_stop(clean_driver)
        short = [p for kind in ("opening", "body", "close") for p in pieces.get(kind, [])]
        recorder = _FramesRecorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder):
            resp = client.post(
                f"/api/v1/audio/generate-trip-stops/{RECIPE_TRIP_ID}", headers=_bearer()
            )
            keep = client.post(f"/api/v1/audio/stops/{RECIPE_ITEM_ID}/keep-exploring")

        assert resp.status_code == 200, resp.text
        assert resp.json()["generated"] == 1, resp.text
        assert recorder.asked == [(RECIPE_LEAD, None)], (
            "only the tour's own line reaches the voice; the telling, the close and the "
            "longer telling's close are the recipe's recordings"
        )
        item = _recipe_item(clean_driver)
        joined = get_storage("local").read(item["audio_url"])
        assert joined.count(b"ID3") == 1
        frames = FRESH_FRAMES + PIECE_FRAMES * len(short)
        assert item["duration"] == pytest.approx(round(frames * _FRAME_SECONDS, 2))
        assert item["close_url"] == pieces["close"][0].audio_url
        assert item["full_close_url"] == pieces["full_close"][0].audio_url

        assert keep.status_code == 200, keep.text
        full = pieces["full"][0]
        assert keep.json()["audio_url"] == full.audio_url
        assert keep.json()["duration_sec"] == full.audio_duration_sec
        assert len(recorder.asked) == 1, "the longer telling is not voiced again"

    @pytest.mark.parametrize(
        ("asked_for", "voice"),
        [({"voice_id": "alice"}, "alice"), ({"provider": "mock"}, None)],
    )
    def test_a_named_provider_or_voice_voices_every_piece_fresh(
        self, client, clean_driver, _temp_audio_storage, asked_for, voice
    ):
        pieces = _seed_recipe_stop(clean_driver)
        recorder = _FramesRecorder()
        with patch("src.audio.pipeline.get_provider_with_fallback", return_value=recorder):
            resp = client.post(
                f"/api/v1/audio/generate-trip-stops/{RECIPE_TRIP_ID}",
                json=asked_for,
                headers=_bearer(),
            )
            keep = client.post(
                f"/api/v1/audio/stops/{RECIPE_ITEM_ID}/keep-exploring", json=asked_for
            )

        assert resp.status_code == 200, resp.text
        assert keep.status_code == 200, keep.text
        item = _recipe_item(clean_driver)
        full = pieces["full"][0]
        assert recorder.asked == [
            (item["narration"], voice),
            (pieces["close"][0].text, voice),
            (pieces["full_close"][0].text, voice),
            (full.text, voice),
        ]
        recorded = {p.audio_url for group in pieces.values() for p in group}
        fresh = (item["audio_url"], item["close_url"], item["full_close_url"])
        assert not recorded & set(fresh)
        assert keep.json()["audio_url"] not in recorded
