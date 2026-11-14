"""Frozen inputs for the cross-city Premium tour regression.

The manifest fixes customer requests before any provider output exists.  Its
hashes identify inputs; they do not claim that an output is good.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from .contract import TourInput
from .corpus_places import canonical_sha256


class FrozenTourCase(BaseModel):
    """One customer-shaped request selected independently of its output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(..., min_length=1)
    archetype: str = Field(..., min_length=1)
    tour_input: TourInput

    @computed_field
    @property
    def request_sha256(self) -> str:
        return canonical_sha256(
            {
                "case_id": self.case_id,
                "archetype": self.archetype,
                "tour_input": self.tour_input.model_dump(mode="json"),
            }
        )

    @property
    def route_mode(self) -> Literal["open", "round_trip", "fixed_end"]:
        if self.tour_input.round_trip:
            return "round_trip"
        if self.tour_input.end is not None:
            return "fixed_end"
        return "open"


class FrozenTourBatchManifest(BaseModel):
    """Exactly eight diverse requests: four Paris and four New York."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ondoway-tour-batch-requests-v1"]
    cases: tuple[FrozenTourCase, ...]

    @model_validator(mode="after")
    def _batch_shape_is_frozen(self) -> FrozenTourBatchManifest:
        if len(self.cases) != 8:
            raise ValueError("batch must contain exactly eight requests")
        case_ids = tuple(case.case_id for case in self.cases)
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("batch request IDs must be unique")
        request_hashes = tuple(case.request_sha256 for case in self.cases)
        if len(set(request_hashes)) != len(request_hashes):
            raise ValueError("batch requests must be unique")
        city_counts = Counter(case.tour_input.city_slug for case in self.cases)
        if city_counts != {"paris": 4, "new_york": 4}:
            raise ValueError("batch must contain four Paris and four New York requests")
        for city_slug in city_counts:
            modes = {
                case.route_mode
                for case in self.cases
                if case.tour_input.city_slug == city_slug
            }
            if modes != {"open", "round_trip", "fixed_end"}:
                raise ValueError(f"{city_slug} batch lacks route-mode diversity")
        return self

    @computed_field
    @property
    def manifest_sha256(self) -> str:
        return canonical_sha256(
            {
                "schema_version": self.schema_version,
                "requests": [case.request_sha256 for case in self.cases],
            }
        )


def load_frozen_tour_batch(path: Path) -> FrozenTourBatchManifest:
    return FrozenTourBatchManifest.model_validate_json(path.read_bytes())
