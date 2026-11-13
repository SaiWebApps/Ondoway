"""Raw authoring candidates have immutable, non-interchangeable identities."""

import hashlib

import pytest
from pydantic import ValidationError

from src.tour.candidate_authoring import (
    AuthoringCandidateIdentity,
    AuthoringCandidatePlan,
    AuthoringCandidateResponseSet,
    AuthoringStopRequest,
    AuthoringStopResponse,
)


def _identity(slot: str) -> AuthoringCandidateIdentity:
    return AuthoringCandidateIdentity.create(
        candidate_slot=slot,
        contract_sha256="1" * 64,
        reference_manifest_sha256="2" * 64,
        calibration_manifest_sha256="3" * 64,
        grounded_source_sha256="4" * 64,
        route_sha256="5" * 64,
        authoring_policy_sha256="6" * 64,
    )


def test_candidate_slots_have_distinct_deterministic_identities() -> None:
    first_a = _identity("A")
    second_a = _identity("A")
    candidate_b = _identity("B")

    assert first_a == second_a
    assert first_a.candidate_id != candidate_b.candidate_id


def test_candidate_identity_cannot_be_forged_or_relabelled() -> None:
    candidate = _identity("A")
    forged = candidate.model_dump(mode="json")
    forged["candidate_slot"] = "B"

    with pytest.raises(ValidationError, match="differs from its frozen authoring inputs"):
        AuthoringCandidateIdentity.model_validate(forged)


def test_each_stop_request_is_immutable_and_candidate_bound() -> None:
    candidate_a = _identity("A")
    candidate_b = _identity("B")
    request_a = AuthoringStopRequest.create(
        candidate=candidate_a,
        stop_index=2,
        compose_input_sha256="7" * 64,
    )
    request_b = AuthoringStopRequest.create(
        candidate=candidate_b,
        stop_index=2,
        compose_input_sha256="7" * 64,
    )

    assert request_a.request_id != request_b.request_id
    forged = request_a.model_dump(mode="json")
    forged["compose_input_sha256"] = "8" * 64
    with pytest.raises(ValidationError, match="differs from its candidate and input"):
        AuthoringStopRequest.model_validate(forged)


def test_stop_response_retains_and_hashes_exact_provider_bytes() -> None:
    request = AuthoringStopRequest.create(
        candidate=_identity("A"),
        stop_index=0,
        compose_input_sha256="7" * 64,
    )
    raw = b'{"sentences":[{"text":"Look up."}]}'
    response = AuthoringStopResponse(
        request=request,
        raw_response=raw,
        raw_response_sha256=hashlib.sha256(raw).hexdigest(),
        parsed_payload_sha256="8" * 64,
    )

    assert response.raw_response == raw
    tampered = response.model_dump(mode="python")
    tampered["raw_response"] = raw + b" "
    with pytest.raises(ValidationError, match="differs from exact provider bytes"):
        AuthoringStopResponse.model_validate(tampered)


def _plan(slot: str) -> AuthoringCandidatePlan:
    candidate = _identity(slot)
    return AuthoringCandidatePlan(
        candidate=candidate,
        stop_requests=tuple(
            AuthoringStopRequest.create(
                candidate=candidate,
                stop_index=index,
                compose_input_sha256=str(index + 7) * 64,
            )
            for index in range(2)
        ),
    )


def test_candidate_a_and_b_have_complete_independent_call_plans() -> None:
    plan_a = _plan("A")
    plan_b = _plan("B")

    assert tuple(item.stop_index for item in plan_a.stop_requests) == (0, 1)
    assert tuple(item.stop_index for item in plan_b.stop_requests) == (0, 1)
    assert {item.request_id for item in plan_a.stop_requests}.isdisjoint(
        item.request_id for item in plan_b.stop_requests
    )


def test_candidate_plan_rejects_one_request_from_another_candidate() -> None:
    plan_a = _plan("A")
    plan_b = _plan("B")

    with pytest.raises(ValidationError, match="request from another candidate"):
        AuthoringCandidatePlan(
            candidate=plan_a.candidate,
            stop_requests=(plan_a.stop_requests[0], plan_b.stop_requests[1]),
        )


def _response(request: AuthoringStopRequest) -> AuthoringStopResponse:
    raw = ('{"stop":' + str(request.stop_index) + '}').encode()
    return AuthoringStopResponse(
        request=request,
        raw_response=raw,
        raw_response_sha256=hashlib.sha256(raw).hexdigest(),
        parsed_payload_sha256="9" * 64,
    )


def test_candidate_response_set_assembles_only_one_complete_plan() -> None:
    plan = _plan("A")
    response_set = AuthoringCandidateResponseSet(
        plan=plan,
        responses=tuple(_response(request) for request in plan.stop_requests),
    )

    assert response_set.plan.candidate.candidate_slot == "A"


def test_candidate_response_set_rejects_missing_or_cross_candidate_stops() -> None:
    plan_a = _plan("A")
    plan_b = _plan("B")

    with pytest.raises(ValidationError, match="exact single-candidate plan"):
        AuthoringCandidateResponseSet(
            plan=plan_a,
            responses=(_response(plan_a.stop_requests[0]),),
        )
    with pytest.raises(ValidationError, match="exact single-candidate plan"):
        AuthoringCandidateResponseSet(
            plan=plan_a,
            responses=(
                _response(plan_a.stop_requests[0]),
                _response(plan_b.stop_requests[1]),
            ),
        )


def _wide_plan(stop_count: int) -> AuthoringCandidatePlan:
    candidate = _identity("A")
    return AuthoringCandidatePlan(
        candidate=candidate,
        stop_requests=tuple(
            AuthoringStopRequest.create(
                candidate=candidate,
                stop_index=index,
                compose_input_sha256=f"{index % 16:x}" * 64,
            )
            for index in range(stop_count)
        ),
    )


def test_no_authoring_surface_imposes_a_stop_ceiling() -> None:
    """"No stop limits, period" — on the AUTHORING side.

    Three ceilings used to sit behind the planner and reject a route it had
    already built: the identity layer's ``MAX_CANDIDATE_STOPS`` (15), the compose
    -request builder's ``AUTHORING_MAX_STOPS`` (15), and the quality scorer's
    ``MAX_COMPOSED_STOPS`` (8). Each only ever REFUSED work the engine had
    legitimately planned, at a point long after the route existed. With duration
    as the only bound on tour length, a 20-stop or 40-stop tour is ordinary, and
    an authoring surface that refuses one is a bug, not a safeguard.

    The LOWER bound survives and is asserted below: a plan with zero stops is
    still a refusal, because the compose-request builder keys its requests by stop
    index and would otherwise return an empty authoring plan in silence.

    UNDO (each turns this RED):
      * re-add ``max_length=15`` to ``stop_requests`` -> assertion 1;
      * re-add ``max_length=15`` to ``responses`` -> assertion 2;
      * restore ``1 <= len(stops) <= AUTHORING_MAX_STOPS`` -> assertion 3;
      * delete the ``if not stops`` lower bound -> assertion 4;
      * restore the ``MAX_COMPOSED_STOPS`` cap term in the C3 floor -> assertion 6.
    """
    import ast
    import pathlib

    # 1 + 2. Twenty stops — five past every deleted authoring ceiling.
    plan = _wide_plan(20)
    assert len(plan.stop_requests) == 20
    response_set = AuthoringCandidateResponseSet(
        plan=plan,
        responses=tuple(_response(request) for request in plan.stop_requests),
    )
    assert len(response_set.responses) == 20

    # 3. The compose-request builder authors a 20-stop route without refusing.
    from src.tour.authoring import _certification_compose_requests
    from tests.test_tour_authoring_from_route import _prebuilt

    stitched, sequence, route = _prebuilt(20)
    _beats, stops, requests = _certification_compose_requests(stitched, sequence, route)
    assert len(stops) == 20
    assert len(requests) == 20

    # 4. The LOWER bound is not collateral damage: an empty stitch still refuses.
    empty = stitched.model_copy(update={"script": ()})
    with pytest.raises(ValueError, match="at least one stop"):
        _certification_compose_requests(empty, sequence, route)

    # 5. STRUCTURAL: neither constant survives, in either module.
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "tour"
    for module, name in (
        ("candidate_authoring.py", "MAX_CANDIDATE_STOPS"),
        ("authoring.py", "AUTHORING_MAX_STOPS"),
        ("quality_rubric.py", "MAX_COMPOSED_STOPS"),
    ):
        tree = ast.parse((src / module).read_text(encoding="utf-8"))
        assigned = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign | ast.AnnAssign)
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            if isinstance(target, ast.Name)
        }
        assert name not in assigned, f"{module} still declares {name}"

    # 6. The quality scorer's thinness floor no longer flattens on long tours. It
    #    used to be capped at what an EIGHT-stop tour could hold, which made C3
    #    progressively weaker the longer the tour — a thin 300-minute tour passed.
    from src.tour.quality_rubric import c3_audio_floor_seconds

    assert c3_audio_floor_seconds(300) > c3_audio_floor_seconds(150) * 1.9, (
        "the C3 thinness floor is still capped by a fixed stop count, so a long "
        "tour is judged against the same word budget as a short one"
    )
