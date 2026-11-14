"""Hostile guards for durable, provider-free certification compose replay."""

from __future__ import annotations

import hashlib

import pytest

from src.tour.artifact import sentences_payload_sha256
from src.tour.authoring import (
    COMPOSE_MODEL,
    CompletedCertificationComposeUnit,
    ComposeRequest,
    candidate_compose_request_envelope,
    compose_input_sha256,
    finalize_certification_composition,
)
from src.tour.candidate_authoring import AuthoringCandidateIdentity, AuthoringStopRequest
from src.tour.contract import (
    POI,
    BeatSequence,
    POIBeats,
    Route,
    Script,
    ScriptPOI,
    Sentence,
    TourInput,
    TransitSegment,
    ValidationReport,
)

RESPONSE_SHA256 = "c" * 64


def _source() -> tuple[Script, Route, BeatSequence, ComposeRequest]:
    stitched = Script(
        city_slug="paris",
        generated_at="2026-07-21T00:00:00Z",
        inputs=TourInput(
            start=(48.85, 2.35), duration_min=10, city_slug="paris"
        ),
        total_audio_seconds=5,
        total_walking_seconds=0,
        total_walk_distance_m=0,
        total_planned_seconds=600,
        selected_pois=(
            ScriptPOI(
                id="p0", name="Square", tier=5, lat=48.85, lng=2.35
            ),
        ),
        lens_coverage={},
        script=(
            Sentence(
                text="And that brings our walk to a close.",
                source_id="GLUE_CLOSING",
                source_type="glue",
                stop_idx=0,
            ),
        ),
        validation=ValidationReport(),
    )
    route = Route(
        pois=(
            POI(
                id="p0",
                name="Square",
                tier=5,
                poi_role="anchor",
                lat=48.85,
                lng=2.35,
            ),
        ),
        transits=(
            TransitSegment(
                from_poi_id=None,
                to_poi_id="p0",
                distance_m=0,
                walk_seconds=0,
            ),
        ),
        total_walk_distance_m=0,
        total_walk_seconds=0,
    )
    request = ComposeRequest(stitched=stitched, tour_context=("Square",))
    return stitched, route, BeatSequence(poi_beats=()), request


def _completed(
    request: ComposeRequest,
    parsed: tuple[Sentence, ...] | None = None,
    *,
    stop_index: int = 0,
) -> CompletedCertificationComposeUnit:
    parsed = parsed or request.stitched.script
    candidate = AuthoringCandidateIdentity.create(
        candidate_slot="A",
        contract_sha256="1" * 64,
        reference_manifest_sha256="2" * 64,
        calibration_manifest_sha256="3" * 64,
        grounded_source_sha256="4" * 64,
        route_sha256="5" * 64,
        authoring_policy_sha256="6" * 64,
    )
    authoring_request = AuthoringStopRequest.create(
        candidate=candidate,
        stop_index=stop_index,
        compose_input_sha256=compose_input_sha256(request),
    )
    envelope, _ = candidate_compose_request_envelope(request, authoring_request)
    return CompletedCertificationComposeUnit(
        unit_id=f"stop:{stop_index}",
        stop_index=stop_index,
        model=COMPOSE_MODEL,
        authorized_request=request,
        authoring_request=authoring_request,
        parsed_provider_sentences=parsed,
        request_sha256=hashlib.sha256(envelope.encode("utf-8")).hexdigest(),
        response_sha256=RESPONSE_SHA256,
        parsed_payload_sha256=sentences_payload_sha256(parsed),
    )


def test_replay_rejects_alternate_request_hash() -> None:
    stitched, route, beats, request = _source()
    unit = _completed(request).model_copy(update={"request_sha256": "b" * 64})

    with pytest.raises(ValueError, match="request hash is inconsistent"):
        finalize_certification_composition(
            stitched,
            beats,
            route,
            completed_units=(unit,),
        )


def test_replay_rejects_alternate_authorized_request_even_with_matching_hash() -> None:
    stitched, route, beats, request = _source()
    alternate_request = request.model_copy(update={"tour_context": ("Elsewhere",)})
    unit = _completed(alternate_request)

    with pytest.raises(ValueError, match="request differs from grounded source"):
        finalize_certification_composition(
            stitched,
            beats,
            route,
            completed_units=(unit,),
        )


@pytest.mark.parametrize("case", ["missing", "extra"])
def test_replay_rejects_missing_or_extra_stop(case: str) -> None:
    stitched, route, beats, request = _source()
    units = [_completed(request)]
    if case == "missing":
        units.clear()
    else:
        extra_sentence = request.stitched.script[0].model_copy(update={"stop_idx": 1})
        extra_script = request.stitched.model_copy(update={"script": (extra_sentence,)})
        extra_request = request.model_copy(update={"stitched": extra_script})
        units.append(_completed(extra_request, stop_index=1))

    with pytest.raises(
        ValueError, match="completed compose responses differ from candidate stops"
    ):
        finalize_certification_composition(
            stitched,
            beats,
            route,
            completed_units=tuple(units),
        )


def test_replay_rejects_parsed_payload_hash_mismatch() -> None:
    stitched, route, beats, request = _source()
    unit = _completed(request).model_copy(update={"parsed_payload_sha256": "d" * 64})

    with pytest.raises(ValueError, match="parsed payload hash is inconsistent"):
        finalize_certification_composition(
            stitched,
            beats,
            route,
            completed_units=(unit,),
        )


def _leg_line_source(
    vignette_text: str,
) -> tuple[Script, Route, BeatSequence, ComposeRequest]:
    """The Verrerie shape: a stop with its own story, plus a walk-past vignette
    one-liner riding the leg into it — the one-liner's text is the variable."""
    from src.tour.contract import BeatRef

    story = BeatRef(
        id="b-story",
        poi_id="p0",
        script_body="The tower is all that remains of the church.",
    )
    vignette = BeatRef(
        id="vig-1",
        poi_id="v1",
        script_body=vignette_text,
    )
    stitched = Script(
        city_slug="paris",
        generated_at="2026-07-21T00:00:00Z",
        inputs=TourInput(start=(48.85, 2.35), duration_min=10, city_slug="paris"),
        total_audio_seconds=10,
        total_walking_seconds=0,
        total_walk_distance_m=0,
        total_planned_seconds=600,
        selected_pois=(
            ScriptPOI(id="p0", name="Square", tier=5, lat=48.85, lng=2.35,
                      beat_ids=("b-story",)),
        ),
        lens_coverage={},
        script=(
            Sentence(text=vignette_text, source_id="vig-1",
                     source_type="beat", stop_idx=0),
            Sentence(text="The tower is all that remains of the church.",
                     source_id="b-story", source_type="beat", stop_idx=0),
            Sentence(text="And that brings our walk to a close.",
                     source_id="GLUE_CLOSING", source_type="glue", stop_idx=0),
        ),
        validation=ValidationReport(),
    )
    route = Route(
        pois=(POI(id="p0", name="Square", tier=5, poi_role="anchor",
                  lat=48.85, lng=2.35),),
        transits=(TransitSegment(from_poi_id=None, to_poi_id="p0",
                                 distance_m=0, walk_seconds=0),),
        total_walk_distance_m=0,
        total_walk_seconds=0,
    )
    beats = BeatSequence(
        poi_beats=(
            POIBeats(
                poi_id="p0", poi_name="Square",
                ordering_strategy="narrative_function", beats=(story,),
            ),
        ),
        vignette_beats={0: (vignette,)},
    )
    request = ComposeRequest(
        stitched=stitched, beats_by_id={"b-story": story}, tour_context=("Square",)
    )
    return stitched, route, beats, request


def test_a_corpus_leg_line_untrue_where_it_plays_is_dropped_not_the_day() -> None:
    """The rule "drop a failing line immediately — silence over wrongness,
    never refuse the day" reaches FIRST compose, for the one
    class where a drop is safe: a walk-concurrent CORPUS unit (a vignette
    one-liner, a transit-beat line — self-contained, so dropping it orphans
    no later sentence). The measured shape: the Verrerie hook's "lived on this street"
    played on a leg, the floor flagged `arrived_word_on_leg:this`, and the
    whole five-stop day refused with a retry that reproduces identically. Now
    the line is left unsaid, the day serves, and the degradations channel says
    so. Writer glue keeps the reroll+refusal path; a stop-piece corpus
    sentence is never dropped here. UNDO: remove the valve from
    finalize_certification_composition -> ComposeVerificationError -> RED."""
    from src.tour.compose_gate import ComposeVerificationError
    from src.tour.degradations import degradation_scope

    del ComposeVerificationError  # imported to name the undo's red, not used on green
    stitched, route, beats, request = _leg_line_source(
        "In the 14th century, a painter lived on this street."
    )
    unit = _completed(request)
    with degradation_scope() as rows:
        result = finalize_certification_composition(
            stitched,
            beats,
            route,
            completed_units=(unit,),
            enforce_placement_floors=True,
        )
    texts = [s.text for s in result.script.script]
    assert not any("lived on this street" in t for t in texts), texts
    assert any("remains of the church" in t for t in texts), texts
    assert any(r.kind == "line_dropped_where_untrue" for r in rows), (
        [r.kind for r in rows]
    )


def test_a_true_leg_line_is_untouched_and_a_stop_piece_fault_still_refuses() -> None:
    """The valve's edges: a leg one-liner with no arrived words ships as
    written, and a placement fault inside a STOP piece (continuous corpus
    prose, where a dropped sentence orphans the ones after it) still refuses
    the compose."""
    from src.tour.compose_gate import ComposeVerificationError

    stitched, route, beats, request = _leg_line_source(
        "In the 14th century, a painter lived on the Rue de la Verrerie."
    )
    unit = _completed(request)
    result = finalize_certification_composition(
        stitched, beats, route, completed_units=(unit,),
        enforce_placement_floors=True,
    )
    assert any("Rue de la Verrerie" in s.text for s in result.script.script)

    _stitched2, route2, beats2, request2 = _leg_line_source(
        "In the 14th century, a painter lived on the Rue de la Verrerie."
    )
    bad = tuple(
        s.model_copy(update={"text": "Walk up the lane to the tower door."})
        if s.source_id == "b-story"
        else s
        for s in request2.stitched.script
    )
    bad_script = request2.stitched.model_copy(update={"script": bad})
    bad_request = request2.model_copy(update={"stitched": bad_script})
    unit2 = _completed(bad_request)
    with pytest.raises(ComposeVerificationError):
        finalize_certification_composition(
            bad_script, beats2, route2, completed_units=(unit2,),
            enforce_placement_floors=True,
        )


def test_replay_preserves_provider_authored_prose_without_editorial_rewrite() -> None:
    stitched, route, beats, request = _source()
    provider_authored = (
        request.stitched.script[0].model_copy(
            update={"text": "The museum closes at five."}
        ),
    )
    unit = _completed(request, provider_authored)

    result = finalize_certification_composition(
        stitched,
        beats,
        route,
        completed_units=(unit,),
    )

    assert result.script.script == provider_authored
    assert result.composition_trace[0].source_sentences == provider_authored
    assert result.composition_trace[0].source_sentence_indexes == (0,)
