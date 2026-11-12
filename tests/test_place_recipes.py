"""Place recipes: one telling per place and lens, built once, stored, and reused.

The writer and the voice are test doubles defined here. The fact checker is the
offline one the money guard installs. Everything else is the product's own code:
the planner, the tour gates, the piece cutter, the graph writes and the build loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from src.connection import get_database
from src.tour.authoring import COMPOSE_MODEL
from src.tour.certification_provider import PhysicalProviderResponse
from src.tour.contract import POI, BeatRef
from src.tour.generation import GLUE_CLOSING
from src.tour.place_recipes import (
    OPENING_WORDS,
    plan_place_recipe,
    recipe_lenses,
    split_pieces,
)
from src.tour.premium_tour import PremiumBuildIdentity
from src.tour.recipe_build import RecipeOutcome, build_recipes, places_to_build
from src.tour.selection import CorpusSnapshot
from src.tour.verify import MockFaithfulnessChecker

CITY = "test_city"
VOICE = "test-voice"
SNAPSHOT_SHA = "0" * 64


def _poi(poi_id: str, name: str) -> POI:
    return POI(id=poi_id, name=name, tier=5, poi_role="stop", lat=40.7, lng=-74.0)


def _beat(poi_id: str, index: int, lens: str, body: str) -> BeatRef:
    return BeatRef(
        id=f"{poi_id}-beat-{index}",
        poi_id=poi_id,
        lenses=(lens,),
        script_body=body,
        word_count=len(body.split()),
        est_spoken_seconds=len(body.split()) // 2,
        narrative_function="anchor",
        key_claims=(body,),
        active_status="active",
    )


HALL_BEATS = (
    ("historic_arch", "The hall was finished in 1812 after nine years of building."),
    ("historic_arch", "Its marble front faces the park across a wide stone plaza."),
    ("hidden_history", "A printer once ran a secret press in the cellar beneath it."),
)
BRIDGE_BEATS = (("waterways_views", "The bridge opened in 1883 and carried trains across."),)


def _snapshot(hall_beats=HALL_BEATS) -> CorpusSnapshot:
    hall, bridge = _poi("hall", "Test Hall"), _poi("bridge", "Test Bridge")
    return CorpusSnapshot(
        pois=(hall, bridge),
        beats_by_poi={
            "hall": tuple(
                _beat("hall", i, lens, body) for i, (lens, body) in enumerate(hall_beats)
            ),
            "bridge": tuple(
                _beat("bridge", i, lens, body) for i, (lens, body) in enumerate(BRIDGE_BEATS)
            ),
        },
        area_types={},
        adjacent_areas={},
    )


class _Writer:
    """Answers each request with its own beat sentences and a written close."""

    cost_bearing = False
    provider_name = "offline"

    def __init__(self, *, close: bool = True) -> None:
        self.calls = 0
        self.close = close

    def execute(self, unit):
        self.calls += 1
        stitched = unit.authorized_request.stitched.script
        sentences = [s.model_dump(mode="json") for s in stitched if s.source_type == "beat"]
        if self.close:
            sentences.append(
                {
                    "text": f"That's {unit.poi_name}, built to last.",
                    "source_id": GLUE_CLOSING,
                    "source_type": "glue",
                    "stop_idx": unit.stop_index,
                    "also_cites": [],
                }
            )
        body = json.dumps({"sentences": sentences}, sort_keys=True).encode("utf-8")
        return PhysicalProviderResponse(
            body=body,
            input_tokens=10,
            output_tokens=100,
            latency_ms=0,
            model=COMPOSE_MODEL,
            provider_request_id=f"test-{self.calls}",
            stop_reason="end_turn",
        )


@dataclass(frozen=True)
class _Audio:
    audio_url: str
    duration_sec: float
    provider: str = "test-voice"


class _Voice:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, text: str, *, stop_key: str, poi_name: str) -> _Audio:
        self.calls.append(text)
        return _Audio(audio_url=f"/audio/{stop_key}.mp3", duration_sec=len(text.split()) / 2.5)


def _build(session, snapshot, writer, voice, seen: list[RecipeOutcome] | None = None):
    return list(
        build_recipes(
            session,
            snapshot,
            places_to_build(snapshot),
            city_slug=CITY,
            snapshot_sha256=SNAPSHOT_SHA,
            executor=writer,
            faithfulness_checker=MockFaithfulnessChecker(),
            voice=voice,
            voice_identity=VOICE,
            build_identity=PremiumBuildIdentity(commit_sha="0" * 40),
            dry_run=False,
            should_continue=lambda outcome: (
                seen.append(outcome) is None if seen is not None else True
            ),
        )
    )


def _seed_graph(session, snapshot: CorpusSnapshot) -> None:
    session.run("MATCH (n) DETACH DELETE n")
    for poi in snapshot.pois:
        session.run("CREATE (:POI {id: $id, name: $name})", id=poi.id, name=poi.name)
        for beat in snapshot.beats_by_poi[poi.id]:
            session.run(
                "MATCH (p:POI {id: $poi}) CREATE (p)-[:HAS_BEAT]->(:NarrativeBeat {id: $id})",
                poi=poi.id,
                id=beat.id,
            )


# ── planning, with no graph and no provider ──


def test_a_place_gets_a_recipe_for_no_lens_and_for_each_lens_its_beats_carry():
    snapshot = _snapshot()
    hall = snapshot.pois[0]
    assert recipe_lenses(hall, snapshot) == (None, "hidden_history", "historic_arch")


def test_planning_twice_from_scratch_gives_the_same_request_with_no_tour_in_it():
    first = plan_place_recipe(
        _snapshot().pois[0],
        _snapshot(),
        lens="historic_arch",
        city_slug=CITY,
        snapshot_sha256=SNAPSHOT_SHA,
        voice_identity=VOICE,
    )
    second = plan_place_recipe(
        _snapshot().pois[0],
        _snapshot(),
        lens="historic_arch",
        city_slug=CITY,
        snapshot_sha256=SNAPSHOT_SHA,
        voice_identity=VOICE,
    )
    assert first.input_sha256 == second.input_sha256
    request = first.plan.units[0].authorized_request
    kinds = {
        (s.source_type, s.source_id) for s in request.stitched.script if s.source_type != "beat"
    }
    assert kinds == {("glue", GLUE_CLOSING)}, "only the beats and the close: no opener, no walk"
    assert request.thread_from == () and request.slots == ()
    assert request.tour_context == (), "no walk, so the writer never closes a day"
    from src.tour.authoring import _compose_user_prompt

    prompt = _compose_user_prompt(request, 1, None)
    assert "TOUR CONTEXT" not in prompt
    assert "PLACE ON ITS OWN" in prompt and "never the day's last stop" in prompt.lower()
    assert "OUTSIDE" in request.door_state, "the writer is told to stage the exterior"


def test_the_voice_is_part_of_what_makes_a_recipe_current():
    hall = _snapshot().pois[0]
    one = plan_place_recipe(
        hall,
        _snapshot(),
        lens=None,
        city_slug=CITY,
        snapshot_sha256=SNAPSHOT_SHA,
        voice_identity="voice-a",
    )
    two = plan_place_recipe(
        hall,
        _snapshot(),
        lens=None,
        city_slug=CITY,
        snapshot_sha256=SNAPSHOT_SHA,
        voice_identity="voice-b",
    )
    assert one.input_sha256 != two.input_sha256


def test_pieces_cut_at_sentence_boundaries_with_the_close_last():
    from src.tour.contract import Sentence

    words = " ".join(["word"] * 30)
    body = [
        Sentence(text=f"{words} {i}.", source_id=f"b{i}", source_type="beat", stop_idx=0)
        for i in range(8)
    ]
    close = Sentence(text="That's it.", source_id=GLUE_CLOSING, source_type="glue", stop_idx=0)
    sentences = (*body, close)
    pieces = split_pieces(sentences)
    assert [p.kind for p in pieces] == ["opening", "body", "body", "close"]
    assert len(pieces[0].text.split()) >= OPENING_WORDS
    assert pieces[0].beat_ids == ("b0", "b1", "b2", "b3")
    assert pieces[-1].text == "That's it." and pieces[-1].beat_ids == ()
    assert " ".join(p.text for p in pieces) == " ".join(s.text for s in sentences)


# ── the build job against the test graph ──


@pytest.fixture()
def session(clean_driver):
    with clean_driver.session(database=get_database()) as s:
        yield s


def test_a_build_stores_every_recipe_and_a_rerun_spends_nothing(session):
    snapshot = _snapshot()
    _seed_graph(session, snapshot)
    writer, voice = _Writer(), _Voice()

    first = _build(session, snapshot, writer, voice)
    assert [o.status for o in first] == ["built"] * 5, "the hall: 3 recipes; the bridge: 2"
    stored = session.run(
        """
        MATCH (r:PlaceRecipe)-[:FOR_PLACE]->(poi:POI)
        MATCH (r)-[h:HAS_PIECE]->(p:RecipePiece)
        RETURN r.id AS id, poi.id AS poi, count(p) AS pieces, count(p.audio_url) AS voiced,
               collect(p.kind) AS kinds
        """
    ).data()
    assert len(stored) == 5
    assert all(row["pieces"] == row["voiced"] > 0 for row in stored)
    assert all("close" in row["kinds"] for row in stored)
    cites = session.run("MATCH (:RecipePiece)-[:CITES]->(b:NarrativeBeat) RETURN count(b) AS n")
    assert cites.single()["n"] > 0
    writes, voicings = writer.calls, len(voice.calls)

    again = _build(session, snapshot, writer, voice)
    assert [o.status for o in again] == ["current"] * 5
    assert writer.calls == writes and len(voice.calls) == voicings, "a rerun costs nothing"


def test_a_changed_beat_rebuilds_only_the_recipes_of_its_place(session):
    snapshot = _snapshot()
    _seed_graph(session, snapshot)
    writer, voice = _Writer(), _Voice()
    _build(session, snapshot, writer, voice)

    edited = list(HALL_BEATS)
    edited[2] = ("hidden_history", "A printer once ran two secret presses in the cellar.")
    changed = _snapshot(hall_beats=tuple(edited))
    outcomes = {(o.poi_name, o.lens): o.status for o in _build(session, changed, writer, voice)}
    assert outcomes[("Test Bridge", None)] == "current"
    assert outcomes[("Test Bridge", "waterways_views")] == "current"
    assert outcomes[("Test Hall", "hidden_history")] == "built"
    count = session.run("MATCH (r:PlaceRecipe) RETURN count(r) AS n").single()["n"]
    assert count == 5, "a rebuild replaces its recipe; no stale copy stays behind"


def test_a_telling_the_checks_refuse_is_never_stored(session):
    snapshot = _snapshot()
    _seed_graph(session, snapshot)
    seen: list[RecipeOutcome] = []
    outcomes = _build(session, snapshot, _Writer(close=False), _Voice(), seen)
    assert {o.status for o in outcomes} == {"refused"}
    assert all(o.attempts == 2 and o.output_tokens == 200 for o in outcomes), (
        "one re-roll, and the spend of both attempts is reported"
    )
    assert len(seen) == len(outcomes), "every paid outcome reaches the stop check"
    assert session.run("MATCH (r:PlaceRecipe) RETURN count(r) AS n").single()["n"] == 0


def test_a_tour_request_keeps_its_exact_bytes_and_hash():
    """The told-on-its-own field leaves every tour request untouched: it is absent
    from the dump while off, so sealed request hashes still match."""
    recipe = plan_place_recipe(
        _snapshot().pois[0],
        _snapshot(),
        lens=None,
        city_slug=CITY,
        snapshot_sha256=SNAPSHOT_SHA,
        voice_identity=VOICE,
    )
    request = recipe.plan.units[0].authorized_request
    assert request.model_dump(mode="json")["standalone_place"] is True
    as_tour = request.model_copy(update={"standalone_place": False})
    assert "standalone_place" not in as_tour.model_dump(mode="json")
