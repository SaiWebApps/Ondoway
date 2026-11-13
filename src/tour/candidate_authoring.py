"""Immutable identities for independent raw-LLM tour candidates."""

from __future__ import annotations

import hashlib
import itertools
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _identity_digest(values: dict[str, str]) -> str:
    payload = json.dumps(values, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_sha256(name: str, value: str) -> None:
    try:
        parsed = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{name} is not a lowercase SHA-256") from exc
    if len(parsed) != 32 or value != value.lower():
        raise ValueError(f"{name} is not a lowercase SHA-256")


class AuthoringCandidateIdentity(BaseModel):
    """One candidate slot bound to every frozen authoring authority hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_slot: Literal["A", "B", "C"]
    contract_sha256: str = Field(..., min_length=64, max_length=64)
    reference_manifest_sha256: str = Field(..., min_length=64, max_length=64)
    calibration_manifest_sha256: str = Field(..., min_length=64, max_length=64)
    grounded_source_sha256: str = Field(..., min_length=64, max_length=64)
    route_sha256: str = Field(..., min_length=64, max_length=64)
    authoring_policy_sha256: str = Field(..., min_length=64, max_length=64)
    candidate_id: str = Field(..., min_length=74, max_length=74)

    @classmethod
    def create(
        cls,
        *,
        candidate_slot: Literal["A", "B", "C"],
        contract_sha256: str,
        reference_manifest_sha256: str,
        calibration_manifest_sha256: str,
        grounded_source_sha256: str,
        route_sha256: str,
        authoring_policy_sha256: str,
    ) -> AuthoringCandidateIdentity:
        values = {
            "candidate_slot": candidate_slot,
            "contract_sha256": contract_sha256,
            "reference_manifest_sha256": reference_manifest_sha256,
            "calibration_manifest_sha256": calibration_manifest_sha256,
            "grounded_source_sha256": grounded_source_sha256,
            "route_sha256": route_sha256,
            "authoring_policy_sha256": authoring_policy_sha256,
        }
        return cls(**values, candidate_id="candidate-" + _identity_digest(values))

    @model_validator(mode="after")
    def _identity_is_canonical(self) -> AuthoringCandidateIdentity:
        values = self.model_dump(mode="json", exclude={"candidate_id"})
        for name, value in values.items():
            if name == "candidate_slot":
                continue
            _require_sha256(name, value)
        expected = "candidate-" + _identity_digest(values)
        if self.candidate_id != expected:
            raise ValueError("candidate id differs from its frozen authoring inputs")
        return self


class AuthoringStopRequest(BaseModel):
    """Immutable identity of one candidate's exact per-stop authoring input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: AuthoringCandidateIdentity
    stop_index: int = Field(..., ge=0)
    compose_input_sha256: str = Field(..., min_length=64, max_length=64)
    request_id: str = Field(..., min_length=72, max_length=72)

    @classmethod
    def create(
        cls,
        *,
        candidate: AuthoringCandidateIdentity,
        stop_index: int,
        compose_input_sha256: str,
    ) -> AuthoringStopRequest:
        values = {
            "candidate_id": candidate.candidate_id,
            "stop_index": str(stop_index),
            "compose_input_sha256": compose_input_sha256,
        }
        return cls(
            candidate=candidate,
            stop_index=stop_index,
            compose_input_sha256=compose_input_sha256,
            request_id="request-" + _identity_digest(values),
        )

    @model_validator(mode="after")
    def _request_is_canonical(self) -> AuthoringStopRequest:
        _require_sha256("compose_input_sha256", self.compose_input_sha256)
        values = {
            "candidate_id": self.candidate.candidate_id,
            "stop_index": str(self.stop_index),
            "compose_input_sha256": self.compose_input_sha256,
        }
        if self.request_id != "request-" + _identity_digest(values):
            raise ValueError("stop request id differs from its candidate and input")
        return self


class AuthoringStopResponse(BaseModel):
    """Exact immutable provider bytes returned for one authorized stop request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: AuthoringStopRequest
    raw_response: bytes = Field(..., min_length=1)
    raw_response_sha256: str = Field(..., min_length=64, max_length=64)
    parsed_payload_sha256: str = Field(..., min_length=64, max_length=64)
    provider_request_id: str | None = None

    @model_validator(mode="after")
    def _response_bytes_are_exact(self) -> AuthoringStopResponse:
        _require_sha256("parsed_payload_sha256", self.parsed_payload_sha256)
        _require_sha256("raw_response_sha256", self.raw_response_sha256)
        if hashlib.sha256(self.raw_response).hexdigest() != self.raw_response_sha256:
            raise ValueError("raw response hash differs from exact provider bytes")
        return self


class AuthoringCandidatePlan(BaseModel):
    """The complete ordered request set for exactly one raw-LLM candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate: AuthoringCandidateIdentity
    # No upper bound: a plan must be able to hold every stop the planner seated,
    # and since 2026-08-04 duration alone decides how many that is. ``min_length=1``
    # stays — a zero-stop plan is a bug, not a short tour.
    stop_requests: tuple[AuthoringStopRequest, ...] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _plan_is_complete_and_single_candidate(self) -> AuthoringCandidatePlan:
        if any(request.candidate != self.candidate for request in self.stop_requests):
            raise ValueError("candidate plan contains a request from another candidate")
        indexes = tuple(request.stop_index for request in self.stop_requests)
        # Strictly increasing, never dense-from-zero: a DAY plan is forced to
        # 0..n-1 by plan_premium_authoring's own coverage checks (one unit per
        # dwell stop, highest index in range), while a FULL-TELLING plan
        # is one stop of the day at its own index —
        # stop 3's second story is a plan of exactly (3,).
        if any(b <= a for a, b in itertools.pairwise(indexes)):
            raise ValueError("candidate plan must cover every stop once in order")
        request_ids = tuple(request.request_id for request in self.stop_requests)
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("candidate plan repeats a stop request")
        return self


class AuthoringCandidateResponseSet(BaseModel):
    """Complete raw responses for one plan; no stop can come from another run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan: AuthoringCandidatePlan
    responses: tuple[AuthoringStopResponse, ...] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _responses_exactly_match_plan(self) -> AuthoringCandidateResponseSet:
        planned = self.plan.stop_requests
        received = tuple(response.request for response in self.responses)
        if received != planned:
            raise ValueError(
                "candidate responses must cover the exact single-candidate plan"
            )
        return self


__all__ = [
    "AuthoringCandidateIdentity",
    "AuthoringCandidatePlan",
    "AuthoringCandidateResponseSet",
    "AuthoringStopRequest",
    "AuthoringStopResponse",
]
