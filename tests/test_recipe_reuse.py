"""A tour stop served from its stored place recipe, and its audio built from the
recipe's recorded pieces.

The writer and the voice are test doubles defined here and in
``tests/test_place_recipes.py``. The fact checker is the offline one the money guard
installs. The planner, the reuse rules, the tour finalizer and its gates, the graph
writes and the audio joiner are the product's own code.
"""

from __future__ import annotations

import dataclasses
import json
import struct
from typing import ClassVar

import pytest

from src.audio.pipeline import assemble_stop_audio
from src.audio.provider import register_provider
from src.audio.storage import get_storage
from src.connection import get_database
from src.tour.authoring import COMPOSE_MODEL
from src.tour.contract import BeatRef, BeatSequence, PhysicalCue, Sentence, TourInput
from src.tour.generation import GLUE_CLOSING, GLUE_STAGING, generate
from src.tour.place_recipes import (
    finalize_place_recipe,
    plan_place_full_telling,
    plan_place_recipe,
    split_pieces,
)
from src.tour.premium_tour import (
    EphemeralReceiptSink,
    PremiumBuildIdentity,
    SilentGlueClient,
    execute_premium_plan,
    finalize_premium_full_telling,
    finalize_premium_tour,
    full_telling_budget,
    full_telling_sequence,
    plan_premium_authoring,
    plan_premium_full_telling,
)
from src.tour.recipe_reuse import (
    RecipeFirstExecutor,
    StoredPiece,
    StoredRecipe,
    _stop_response,
    audio_parts,
    full_telling_use,
    plan_recipe_reuse,
    stored_full_telling,
)
from src.tour.recipe_store import (
    current_input_sha256,
    link_stops_to_recipes,
    served_recipes,
    stop_recipe_pieces,
)
from src.tour.routing import summarise_route
from src.tour.selection import CorpusSnapshot, build_poi_beat_plans_capped
from src.tour.verify import MockFaithfulnessChecker
from tests.test_place_recipes import (
    CITY,
    SNAPSHOT_SHA,
    VOICE,
    _beat,
    _build,
    _poi,
    _seed_graph,
    _Voice,
    _Writer,
)

PLACES = (
    ("hall", "Test Hall", 40.7000, -74.0000),
    ("bridge", "Test Bridge", 40.7030, -74.0000),
    ("tower", "Test Tower", 40.7060, -74.0000),
)
BEATS = {
    "hall": (
        ("historic_arch", "The hall was finished in 1812 after nine years of building."),
        ("historic_arch", "Its marble front faces the park across a wide stone plaza."),
    ),
    "bridge": (("waterways_views", "The bridge opened in 1883 and carried trains across."),),
    "tower": (("historic_arch", "The tower was raised in 1931 as a lookout over the bay."),),
}


def _piece(kind: str, text: str, url: str) -> StoredPiece:
    return StoredPiece(kind=kind, text=text, audio_url=url, audio_duration_sec=1.0)


def _staged(beat: BeatRef) -> BeatRef:
    """The hall's first beat names something to look at, so the tour stages it."""
    if beat.id != "hall-beat-0":
        return beat
    cue = PhysicalCue(cue="Look up at the marble front", feature_type="view")
    return beat.model_copy(update={"physical_cues": (cue,)})


def _snapshot(beats=BEATS) -> CorpusSnapshot:
    pois = []
    for poi_id, name, lat, lng in PLACES:
        poi = _poi(poi_id, name).model_copy(update={"lat": lat, "lng": lng})
        pois.append(poi)
    return CorpusSnapshot(
        pois=tuple(pois),
        beats_by_poi={
            poi_id: tuple(
                _staged(_beat(poi_id, i, lens, body)) for i, (lens, body) in enumerate(rows)
            )
            for poi_id, rows in beats.items()
        },
        area_types={},
        adjacent_areas={},
    )


def _tour(snapshot: CorpusSnapshot, *, inside: frozenset[str] = frozenset(), lenses=None):
    """A three-stop solo walk through the snapshot's places, planned for free."""

    tour_input = TourInput(
        start=(40.6990, -74.0000),
        duration_min=60,
        city_slug=CITY,
        lenses=lenses,
        narration_register="solo",
    )
    route = summarise_route(
        list(snapshot.pois),
        start_lat=tour_input.start[0],
        start_lng=tour_input.start[1],
        round_trip=False,
        duration_min=tour_input.duration_min,
        spine_area=None,
        routing_client=None,
        visit_goes_inside={poi.id: poi.id in inside for poi in snapshot.pois},
    )
    capped = build_poi_beat_plans_capped(
        route, snapshot, lenses=tour_input.lenses, end_is_none=True
    )
    sequence = BeatSequence(
        poi_beats=tuple(plan for plan, _ in capped),
        overflow_by_poi={plan.poi_id: overflow for plan, overflow in capped if overflow},
    )
    source = generate(
        sequence, route, tour_input, glue_client=SilentGlueClient(), validate_output=False
    )
    return plan_premium_authoring(
        source,
        sequence,
        route,
        snapshot=snapshot,
        snapshot_sha256=SNAPSHOT_SHA,
        routing_version="test",
        policy_version="test",
    )


def _stored(snapshot: CorpusSnapshot, poi_index: int, lens=None) -> StoredRecipe:
    """One place's recipe, written by the test writer and checked by the tour gates."""

    recipe = plan_place_recipe(
        snapshot.pois[poi_index],
        snapshot,
        lens=lens,
        city_slug=CITY,
        snapshot_sha256=SNAPSHOT_SHA,
        voice_identity=VOICE,
    )
    responses = execute_premium_plan(
        recipe.plan, executor=_Writer(), receipt_sink=EphemeralReceiptSink()
    )
    sentences = finalize_place_recipe(
        recipe, responses, faithfulness_checker=MockFaithfulnessChecker()
    )
    return StoredRecipe(
        recipe_id=recipe.recipe_id,
        input_sha256=recipe.input_sha256,
        compose_model=COMPOSE_MODEL,
        sentences=sentences,
    )


def _all_stored(snapshot: CorpusSnapshot) -> dict[str, StoredRecipe]:
    stored = (_stored(snapshot, i) for i in range(len(snapshot.pois)))
    return {recipe.recipe_id: recipe for recipe in stored}


# ── which stops a recipe serves ──


def test_matching_stops_take_their_recipe_and_the_last_stop_never_does():
    snapshot = _snapshot()
    plan = _tour(snapshot)
    uses = plan_recipe_reuse(plan, _all_stored(snapshot), voice_identity=VOICE)
    assert sorted(uses) == [0, 1], "the last stop closes the day, which no recipe does"


def test_a_stop_that_goes_inside_keeps_the_writer():
    snapshot = _snapshot()
    plan = _tour(snapshot, inside=frozenset({"hall"}))
    uses = plan_recipe_reuse(plan, _all_stored(snapshot), voice_identity=VOICE)
    assert 0 not in uses, "a recipe is staged outside the door"
    assert 1 in uses


def test_a_recipe_built_from_other_beats_or_another_voice_is_not_used():
    snapshot = _snapshot()
    stored = _all_stored(snapshot)
    edited = dict(BEATS)
    edited["hall"] = (("historic_arch", "The hall was finished in 1813 after ten years."),)
    changed = _snapshot(edited)
    uses = plan_recipe_reuse(_tour(changed), stored, voice_identity=VOICE)
    assert 0 not in uses and 1 in uses, "only the edited place loses its recipe"
    assert plan_recipe_reuse(_tour(snapshot), stored, voice_identity="other-voice") == {}


def test_a_change_to_the_writing_rules_alone_retires_every_recipe(monkeypatch):
    snapshot = _snapshot()
    stored = _all_stored(snapshot)
    plan = _tour(snapshot)
    assert sorted(plan_recipe_reuse(plan, stored, voice_identity=VOICE)) == [0, 1], "premise"
    monkeypatch.setattr("src.tour.place_recipes.premium_authoring_policy_sha256", lambda: "f" * 64)
    assert plan_recipe_reuse(plan, stored, voice_identity=VOICE) == {}


def test_a_current_recipe_is_not_used_by_a_stop_asking_for_other_beats():
    """The input hash reads only the place, so a stop's own beat set is checked apart
    from it."""
    snapshot = _snapshot()
    stored = _all_stored(snapshot)
    plan = _tour(snapshot)
    assert 0 in plan_recipe_reuse(plan, stored, voice_identity=VOICE), "premise"
    unit = plan.units[0]
    request = unit.authorized_request
    assert len(request.beats_by_id) > 1, "premise: the hall's stop seats two beats"
    fewer = dict(list(request.beats_by_id.items())[:1])
    narrowed = dataclasses.replace(
        unit, authorized_request=request.model_copy(update={"beats_by_id": fewer})
    )
    other_beats = dataclasses.replace(plan, units=(narrowed, *plan.units[1:]))
    uses = plan_recipe_reuse(other_beats, stored, voice_identity=VOICE)
    assert 0 not in uses and 1 in uses


def test_a_current_recipe_written_by_another_model_is_not_used():
    snapshot = _snapshot()
    stored = _all_stored(snapshot)
    plan = _tour(snapshot)
    hall = plan_recipe_reuse(plan, stored, voice_identity=VOICE)[0].recipe_id
    older = {**stored, hall: dataclasses.replace(stored[hall], compose_model="older-model")}
    uses = plan_recipe_reuse(plan, older, voice_identity=VOICE)
    assert 0 not in uses and 1 in uses


def test_a_tour_for_another_walker_or_several_lenses_takes_no_recipe():
    snapshot = _snapshot()
    stored = _all_stored(snapshot)
    plan = _tour(snapshot)
    family = plan.tour_input.model_copy(update={"narration_register": "family"})
    assert (
        plan_recipe_reuse(
            type(plan)(**{**plan.__dict__, "tour_input": family}), stored, voice_identity=VOICE
        )
        == {}
    )
    two = _tour(snapshot, lenses=["historic_arch", "waterways_views"])
    assert plan_recipe_reuse(two, stored, voice_identity=VOICE) == {}


# ── a reused stop passes the tour's own checks ──


def test_a_tour_told_from_recipes_passes_the_tour_gates_and_calls_the_writer_less():
    snapshot = _snapshot()
    stored = _all_stored(snapshot)
    plan = _tour(snapshot)
    uses = plan_recipe_reuse(plan, stored, voice_identity=VOICE)
    writer = _Writer()
    responses = execute_premium_plan(
        plan, executor=RecipeFirstExecutor(writer, uses), receipt_sink=EphemeralReceiptSink()
    )
    assert writer.calls == 1, "only the last stop was written"
    result = finalize_premium_tour(
        plan,
        responses,
        faithfulness_checker=MockFaithfulnessChecker(),
        build_identity=PremiumBuildIdentity(commit_sha="0" * 40),
    )
    script = result.blueprint.script.script
    for stop_index, use in uses.items():
        recipe = next(r for r in stored.values() if r.recipe_id == use.recipe_id)
        told = [s.text for s in script if s.stop_idx == stop_index and s.source_type == "beat"]
        assert told == [s.text for s in recipe.sentences if s.source_type == "beat"]
        close = [s.text for s in script if s.stop_idx == stop_index and s.source_id == GLUE_CLOSING]
        assert close == [recipe.sentences[-1].text]
    walk_lines = [s for s in script if s.stop_idx == 1 and s.source_id == "GLUE_NAV"]
    assert walk_lines, "the stop keeps the tour's own walking line"
    staged = [s for s in plan.source.script if s.stop_idx == 0 and s.source_id == GLUE_STAGING]
    assert staged, "premise: the tour's first stop has a staging line"
    assert not [s for s in script if s.stop_idx in uses and s.source_id == GLUE_STAGING], (
        "a recipe stages its own place, so the tour's staging line is left out"
    )


def test_a_reused_stop_keeps_the_sights_passed_on_the_walk_in():
    """A sight passed on the way is a beat of no stop; the recipe does not tell it,
    so the stop's own walking lines carry it through."""
    snapshot = _snapshot()
    stored = _all_stored(snapshot)
    plan = _tour(snapshot)
    unit = plan.units[1]
    request = unit.authorized_request
    passed = Sentence(
        text="On the way you pass the old fountain.",
        source_id="fountain-beat-0",
        source_type="beat",
        stop_idx=1,
    )
    walk_in = request.model_copy(
        update={
            "stitched": request.stitched.model_copy(
                update={"script": (passed, *request.stitched.script)}
            )
        }
    )
    recipe = stored[plan_recipe_reuse(plan, stored, voice_identity=VOICE)[1].recipe_id]
    response = _stop_response(dataclasses.replace(unit, authorized_request=walk_in), recipe)
    texts = [s["text"] for s in json.loads(response.body)["sentences"]]
    assert texts[0] == passed.text


def test_a_stop_rolled_again_goes_to_the_writer():
    snapshot = _snapshot()
    plan = _tour(snapshot)
    uses = plan_recipe_reuse(plan, _all_stored(snapshot), voice_identity=VOICE)
    writer = _Writer()
    executor = RecipeFirstExecutor(writer, uses)
    first = executor.execute(plan.units[0])
    again = executor.execute(plan.units[0])
    assert first.provider_request_id.startswith("recipe:")
    assert again.provider_request_id.startswith("test-") and writer.calls == 1


# ── the audio: stored pieces reused, only new lines voiced ──


def test_the_spoken_text_splits_into_new_lines_and_stored_pieces():
    pieces = (
        _piece("opening", "The hall was built.", "/a"),
        _piece("close", "That's the hall.", "/b"),
    )
    assert audio_parts("The hall was built. That's the hall.", pieces) == list(pieces)
    assert audio_parts("Settle in. The hall was built. That's the hall.", pieces) == [
        "Settle in.",
        *pieces,
    ]
    assert audio_parts("The hall was rebuilt. That's the hall.", pieces) is None
    assert audio_parts("anything", ()) is None


#: One MPEG-1 Layer III frame at 128 kbps and 44.1 kHz: 417 bytes, 1152 samples.
_FRAME = struct.pack(">I", 0xFFFB9064) + b"\x00" * 413
_FRAME_SECONDS = 1152 / 44100
_TAG = b"ID3\x04\x00\x00\x00\x00\x00\x05" + b"\x00" * 5


class _FrameVoice:
    """Speaks any text as a tagged file of ten frames, and counts what it was asked."""

    asked: ClassVar[list[str]] = []
    name = "frames"

    def generate(self, text: str, *, voice_id: str | None = None) -> bytes:
        del voice_id
        _FrameVoice.asked.append(text)
        return _TAG + _FRAME * 10


def test_stop_audio_joins_stored_pieces_and_voices_only_the_new_line(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIO_STORAGE_PATH", str(tmp_path))
    register_provider("frames", _FrameVoice)
    _FrameVoice.asked = []
    storage = get_storage("local")
    first = _piece(
        "opening", "The hall was built.", storage.upload(_TAG + _FRAME * 3, "pieces/a.mp3")
    )
    second = _piece("close", "That's the hall.", storage.upload(_TAG + _FRAME * 2, "pieces/b.mp3"))

    result = assemble_stop_audio(
        ["Settle in.", first, second],
        stop_key="item-1",
        poi_name="Test Hall",
        provider_name="frames",
    )

    assert _FrameVoice.asked == ["Settle in."], "the stored pieces are never voiced again"
    joined = storage.read(result.audio_url)
    assert joined.count(b"ID3") == 1, "only the file's own leading tag survives the join"
    assert result.duration_sec == pytest.approx(round(15 * _FRAME_SECONDS, 2))


# ── the graph: which recipes a planned tour can take, and which a stop was told from ──


@pytest.fixture()
def session(clean_driver):
    with clean_driver.session(database=get_database()) as s:
        yield s


def test_a_served_stop_links_to_its_recipe_and_reads_back_its_pieces(session):
    snapshot = _snapshot()
    _seed_graph(session, snapshot)
    _build(session, snapshot, _Writer(), _Voice())
    plan = _tour(snapshot)

    uses = served_recipes(session, plan, voice_identity=VOICE)
    assert sorted(uses) == [0, 1]
    assert served_recipes(session, plan, voice_identity=None) == {}

    session.run("CREATE (:ItineraryItem {id: 'item-0'}), (:ItineraryItem {id: 'item-2'})")
    link_stops_to_recipes(session, {"item-0": uses[0].recipe_id})
    pieces = stop_recipe_pieces(session, "item-0")
    stored = session.run(
        "MATCH (r:PlaceRecipe {id: $id}) RETURN r.sentences_json AS s", id=uses[0].recipe_id
    ).single()["s"]
    assert pieces and all(piece.audio_url for piece in pieces)
    assert " ".join(p.text for p in pieces) == " ".join(s["text"] for s in json.loads(stored))
    assert stop_recipe_pieces(session, "item-2") == ()


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(
            "MATCH (:PlaceRecipe {id: $id})-[:HAS_PIECE]->(p) DETACH DELETE p", id="no-pieces"
        ),
        pytest.param(
            "MATCH (:PlaceRecipe {id: $id})-[:HAS_PIECE {order: 0}]->(p) REMOVE p.audio_url",
            id="a-piece-without-audio",
        ),
    ],
)
def test_an_unplayable_recipe_is_neither_current_nor_served_and_a_build_repairs_it(session, damage):
    snapshot = _snapshot()
    _seed_graph(session, snapshot)
    _build(session, snapshot, _Writer(), _Voice())
    hall = "hall:no-lens:solo:outside"
    session.run(damage, id=hall)

    assert current_input_sha256(session, hall) is None
    uses = served_recipes(session, _tour(snapshot), voice_identity=VOICE)
    assert 0 not in uses and 1 in uses, "only the damaged place loses its recipe"

    repaired = {o.recipe_id: o.status for o in _build(session, snapshot, _Writer(), _Voice())}
    assert repaired.pop(hall) == "built"
    assert set(repaired.values()) == {"current"}, "the whole recipes are not rebuilt"
    assert current_input_sha256(session, hall) is not None
    assert sorted(served_recipes(session, _tour(snapshot), voice_identity=VOICE)) == [0, 1]
    orphans = session.run(
        "MATCH (p:RecipePiece) WHERE NOT ()-[:HAS_PIECE]->(p) RETURN count(p) AS n"
    ).single()["n"]
    assert orphans == 0, "the repair leaves no stray piece behind"


def test_piece_split_matches_what_the_store_reads_back():
    """The stored pieces are the cut ``split_pieces`` makes of the stored telling."""
    snapshot = _snapshot()
    recipe = _stored(snapshot, 0)
    pieces = split_pieces(recipe.sentences)
    assert " ".join(p.text for p in pieces) == " ".join(s.text for s in recipe.sentences)


# ── the full telling: a place's second story, stored with its recipe ──

TOWER_FACTS = (
    ("establishing", "The tower was raised in 1931 by a shipping magnate."),
    ("deepen", "Its clock stopped during the blizzard of 1947."),
    ("deepen", "A rooftop garden opened to visitors in 1962."),
    ("climax", "Sailors used its lamp as a harbour mark until 1980."),
)


def _long_beat(poi_id: str, index: int, function: str, body: str) -> BeatRef:
    """A beat long enough that four of them pass the per-stop ceiling, so the cap
    leaves a second story."""
    return BeatRef(
        id=f"{poi_id}-beat-{index}",
        poi_id=poi_id,
        lenses=("historic_arch",),
        script_body=body,
        word_count=200,
        est_spoken_seconds=80,
        narrative_function=function,
        key_claims=(body,),
        active_status="active",
    )


def _tower_first(facts=TOWER_FACTS) -> CorpusSnapshot:
    """A walk whose first stop, the tower, has a second story. Its climax beat comes
    last, so a cap that protected a final stop's closing beat would seat different
    beats in the recipe than at this middle stop."""
    tower = _poi("tower", "Test Tower").model_copy(update={"lat": 40.7000, "lng": -74.0})
    bridge = _poi("bridge", "Test Bridge").model_copy(update={"lat": 40.7030, "lng": -74.0})
    return CorpusSnapshot(
        pois=(tower, bridge),
        beats_by_poi={
            "tower": tuple(_long_beat("tower", i, f, b) for i, (f, b) in enumerate(facts)),
            "bridge": (_beat("bridge", 0, "waterways_views", BEATS["bridge"][0][1]),),
        },
        area_types={},
        adjacent_areas={},
    )


def _stored_with_full(snapshot: CorpusSnapshot, poi_index: int) -> StoredRecipe:
    """A recipe with both tellings, written by the test writer and checked by the
    tour's own short- and full-telling gates."""
    recipe = plan_place_recipe(
        snapshot.pois[poi_index],
        snapshot,
        lens=None,
        city_slug=CITY,
        snapshot_sha256=SNAPSHOT_SHA,
        voice_identity=VOICE,
    )
    checker = MockFaithfulnessChecker()
    sentences = finalize_place_recipe(
        recipe,
        execute_premium_plan(recipe.plan, executor=_Writer(), receipt_sink=EphemeralReceiptSink()),
        faithfulness_checker=checker,
    )
    planned = plan_place_full_telling(recipe, sentences)
    assert planned is not None, "premise: the place has a second story"
    full_plan, budget = planned
    full = finalize_premium_full_telling(
        full_plan,
        execute_premium_plan(full_plan, executor=_Writer(), receipt_sink=EphemeralReceiptSink()),
        budget=budget,
        faithfulness_checker=checker,
    )
    assert full is not None
    return StoredRecipe(
        recipe_id=recipe.recipe_id,
        input_sha256=recipe.input_sha256,
        compose_model=COMPOSE_MODEL,
        sentences=sentences,
        full_sentences=full.sentences,
    )


def _composed_tour(snapshot: CorpusSnapshot, stored: dict[str, StoredRecipe]):
    plan = _tour(snapshot)
    uses = plan_recipe_reuse(plan, stored, voice_identity=VOICE)
    responses = execute_premium_plan(
        plan, executor=RecipeFirstExecutor(_Writer(), uses), receipt_sink=EphemeralReceiptSink()
    )
    composed = finalize_premium_tour(
        plan,
        responses,
        faithfulness_checker=MockFaithfulnessChecker(),
        build_identity=PremiumBuildIdentity(commit_sha="0" * 40),
    ).blueprint.script
    return plan, uses, composed


def _tour_full_plan(snapshot, plan, composed, stop_index: int):
    words = sum(len(s.text.split()) for s in composed.script if s.stop_idx == stop_index)
    budget = full_telling_budget(words)
    full_plan = plan_premium_full_telling(
        composed,
        full_telling_sequence(plan.route, snapshot, plan.sequence),
        plan.route,
        stop_index=stop_index,
        budget=budget,
        snapshot=snapshot,
        snapshot_sha256=SNAPSHOT_SHA,
        routing_version="test",
        policy_version="test",
    )
    return full_plan, budget


def test_a_major_stop_told_from_its_recipe_takes_the_stored_full_telling():
    snapshot = _tower_first()
    stored = _stored_with_full(snapshot, 0)
    plan, uses, composed = _composed_tour(snapshot, {stored.recipe_id: stored})
    assert 0 in uses, "premise: the tower's short telling came from its recipe"
    full_plan, budget = _tour_full_plan(snapshot, plan, composed, 0)

    use = full_telling_use(full_plan, uses[0])
    assert use is not None
    full = finalize_premium_full_telling(
        full_plan, (use.response,), budget=budget, faithfulness_checker=MockFaithfulnessChecker()
    )
    assert full is not None, "the stored full telling passes the tour's own gates"
    assert [s.text for s in full.sentences] == [s.text for s in stored.full_sentences]


def test_a_full_telling_of_another_second_story_is_not_used():
    snapshot = _tower_first()
    stored = _stored_with_full(snapshot, 0)
    plan, uses, composed = _composed_tour(snapshot, {stored.recipe_id: stored})
    full_plan, _budget = _tour_full_plan(snapshot, plan, composed, 0)
    other_story = dataclasses.replace(uses[0], second_story=uses[0].second_story[:1])
    assert full_telling_use(full_plan, other_story) is None
    no_full = dataclasses.replace(stored, full_sentences=())
    assert full_telling_use(full_plan, dataclasses.replace(uses[0], recipe=no_full)) is None


def test_a_full_telling_that_left_a_beat_out_is_still_offered_to_the_gates():
    """The writer may leave one of the second story's beats untold, and the gates
    allow it; the stored telling is still the answer to the same request."""
    snapshot = _tower_first()
    stored = _stored_with_full(snapshot, 0)
    plan, uses, composed = _composed_tour(snapshot, {stored.recipe_id: stored})
    full_plan, _budget = _tour_full_plan(snapshot, plan, composed, 0)
    shorter = dataclasses.replace(stored, full_sentences=stored.full_sentences[-2:])
    assert full_telling_use(full_plan, dataclasses.replace(uses[0], recipe=shorter)) is not None


def test_the_arrival_audio_uses_only_the_short_telling_and_a_tap_gets_the_full_one():
    pieces = (
        _piece("opening", "The tower was raised.", "/a"),
        _piece("close", "That's the tower.", "/b"),
        _piece("full", "Its clock stopped. That's the tower, again.", "/c"),
        _piece("full_close", "That's the tower, again.", "/d"),
    )
    assert audio_parts("The tower was raised. That's the tower.", pieces) == list(pieces[:2])
    assert stored_full_telling("Its clock stopped. That's the tower, again.", pieces) == pieces[2]
    assert stored_full_telling("Its clock stopped.", pieces) is None


def test_a_build_stores_the_full_telling_and_voices_it(session):
    snapshot = _tower_first()
    _seed_graph(session, snapshot)
    voice = _Voice()
    outcomes = {(o.poi_name, o.lens): o for o in _build(session, snapshot, _Writer(), voice)}
    assert outcomes[("Test Tower", None)].full_telling is True
    assert outcomes[("Test Bridge", None)].full_telling is False, "no second story, no full one"
    row = session.run(
        """
        MATCH (r:PlaceRecipe {id: 'tower:no-lens:solo:outside'})-[:HAS_PIECE]->(p)
        RETURN r.full_sentences_json AS full, collect(p.kind) AS kinds,
               count(p.audio_url) AS voiced, count(p) AS pieces
        """
    ).single()
    assert json.loads(row["full"]), "the full telling is stored with the recipe"
    assert {"full", "full_close"} <= set(row["kinds"])
    assert row["voiced"] == row["pieces"]
    again = _build(session, snapshot, _Writer(), voice)
    assert {o.status for o in again} == {"current"}


def _recipe_for(snapshot: CorpusSnapshot, poi_index: int):
    return plan_place_recipe(
        snapshot.pois[poi_index],
        snapshot,
        lens=None,
        city_slug=CITY,
        snapshot_sha256=SNAPSHOT_SHA,
        voice_identity=VOICE,
    )


def _seated_ids(sequence: BeatSequence, poi_id: str) -> list[str]:
    seated = next(plan for plan in sequence.poi_beats if plan.poi_id == poi_id)
    return [beat.id for beat in seated.beats]


def test_a_recipe_seats_the_beats_a_tour_seats_where_the_stop_does_not_close_the_day():
    """A stop takes its recipe only when both ask for the same beats, so a recipe
    caps its beats as a middle stop's are."""
    snapshot = _tower_first()
    recipe = _recipe_for(snapshot, 0)
    tour = _tour(snapshot)
    assert [poi.id for poi in tour.route.pois] == ["tower", "bridge"], "premise"
    assert _seated_ids(recipe.plan.sequence, "tower") == _seated_ids(tour.sequence, "tower")
    assert recipe.plan.sequence.overflow_by_poi["tower"] == tour.sequence.overflow_by_poi["tower"]
    ((closing, _overflow),) = build_poi_beat_plans_capped(
        recipe.plan.route, snapshot, lenses=None, end_is_none=True
    )
    assert [beat.id for beat in closing.beats] != _seated_ids(recipe.plan.sequence, "tower"), (
        "premise: a stop that closes the day seats other beats, so this test can tell"
    )


def test_a_change_to_the_second_story_alone_retires_the_recipe():
    """The longer telling's beats join the input hash although the short telling's
    request never names them."""
    snapshot = _tower_first()
    before = _recipe_for(snapshot, 0)
    stored = _stored(snapshot, 0)
    assert 0 in plan_recipe_reuse(_tour(snapshot), {stored.recipe_id: stored}, voice_identity=VOICE)
    later = before.plan.sequence.overflow_by_poi["tower"][-1]
    index = int(later.rsplit("-", 1)[1])
    facts = list(TOWER_FACTS)
    facts[index] = (facts[index][0], "Sailors used its lamp as a harbour mark until 1985.")
    edited = _tower_first(tuple(facts))

    after = _recipe_for(edited, 0)
    assert after.plan.units[0].request_sha256 == before.plan.units[0].request_sha256, (
        "premise: the short telling's request is unchanged"
    )
    assert _seated_ids(after.plan.sequence, "tower") == _seated_ids(before.plan.sequence, "tower")
    assert after.input_sha256 != before.input_sha256
    uses = plan_recipe_reuse(_tour(edited), {stored.recipe_id: stored}, voice_identity=VOICE)
    assert 0 not in uses
