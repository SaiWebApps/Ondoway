"""The canonical JSON and hash forms every tour hash reads, the one diacritic
fold every name comparison uses, and the place rules the artifact layer
validates its evidence with.

This module imports no other tour module, so every tour module can import it
without a cycle. A hash proves replay identity, not trust: the certification
coordinator must still allow-list ``authority_id`` from the frozen corpus build.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SHA256_PATTERN = r"^[0-9a-f]{64}$"

def canonical_json(value: object) -> str:
    """Return the one JSON representation accepted by corpus hashes."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_bytes(value: object) -> bytes:
    """UTF-8 bytes of :func:`canonical_json`: the one byte form every tour hash reads."""

    return canonical_json(value).encode("utf-8")


def canonical_sha256(value: object) -> str:
    """SHA-256 of :func:`canonical_bytes`."""

    return bytes_sha256(canonical_bytes(value))


def model_sha256(value: BaseModel, **dump_kwargs: object) -> str:
    """SHA-256 of a model's JSON-mode dump in the canonical byte form."""

    return canonical_sha256(value.model_dump(mode="json", **dump_kwargs))


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def strip_diacritics(text: str) -> str:
    """``text`` without its combining marks: "André" and "Andre" are one name.

    Compatibility decomposition (NFKD) first, so a ligature or a full-width letter
    folds to its plain letters, then every character with a non-zero combining
    class is dropped. Letters with no decomposition ("œ", "ß") stay as they are.
    """
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )


def valid_coordinates(value: tuple[float, float]) -> tuple[float, float]:
    """Is this a real point on Earth? THE ONE ANSWER.

    Lives in this module because this module imports no other tour module, so
    every tour module can import it without a cycle.

    It is the one definition, so tightening the rule (a city-scoped bound, a
    coordinate-precision floor, rejecting the null island) is one edit.

    Non-finite values are rejected by the range comparison alone — ``-90 <= nan``
    is False — so ``isfinite`` is belt-and-braces, kept because a reader should not
    have to know that about NaN to trust this.
    """
    lat, lng = value
    if not math.isfinite(lat) or not -90.0 <= lat <= 90.0:
        raise ValueError(f"lat out of range: {lat}")
    if not math.isfinite(lng) or not -180.0 <= lng <= 180.0:
        raise ValueError(f"lng out of range: {lng}")
    return value


def canonical_payload_bound_to_hash(
    payload_json: str,
    payload_sha256: str,
    *,
    label: str,
    require_object: bool = False,
) -> None:
    """Is this payload canonical JSON, and does its hash actually bind it?

    THE ONE ANSWER, and it is a replay-integrity rule rather than a formatting
    one: a payload that is canonical and hash-bound can be re-derived byte for
    byte by anybody holding the hash, which is the whole basis on which evidence
    in this system is trusted.

    Whether the payload must be an object, and the error label, are arguments,
    because they are the only things callers vary.

    ``label`` names the evidence in the error the way each caller already did, so
    a failure still says which kind of payload was wrong.
    """
    try:
        parsed = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not JSON") from exc
    # `canonical_json` and not a local `json.dumps`: this module already owns the
    # one canonical form, and the copies this helper replaced spelled it inline
    # WITHOUT `allow_nan=False`. That was a real difference, not a formatting one —
    # `json.loads` accepts a bare `NaN` literal, so the laxer form round-tripped a
    # payload carrying NaN and hash-bound it as valid evidence. Delegating tightens
    # those callers to the strict form, which is the correct one for anything whose
    # whole purpose is exact replay.
    canonical = canonical_json(parsed)
    if require_object and not isinstance(parsed, dict):
        raise ValueError(f"{label} is not a canonical JSON object")
    if canonical != payload_json:
        raise ValueError(
            f"{label} is not a canonical JSON object"
            if require_object
            else f"{label} is not canonical JSON"
        )
    if payload_sha256 != text_sha256(canonical):
        raise ValueError(f"{label} hash does not match payload")


def check_reference_lane(
    reference_kind: str,
    relation: str | None,
    feature_of_canonical_place_id: str | None,
    perceivability_state: str,
) -> None:
    """The experiential-vs-historical lane rules for a place reference.

    A contextual-historical reference carries no experiential semantics (no
    relation, no feature parent) and marks perceivability not applicable; an
    experiential reference requires a typed relation, a perceivability decision,
    and the feature_of/parent pairing. ``artifact.PlaceReferenceEvidence`` adds its
    directional semantics and coordinate identity beside this call.
    """
    if reference_kind == "contextual_historical":
        if relation is not None or feature_of_canonical_place_id is not None:
            raise ValueError(
                "contextual historical references cannot carry experiential semantics"
            )
        if perceivability_state != "not_applicable":
            raise ValueError(
                "contextual historical references must mark perceivability not applicable"
            )
        return
    if relation is None:
        raise ValueError("experiential references require a typed place relation")
    if perceivability_state == "not_applicable":
        raise ValueError("experiential references require a perceivability decision")
    if relation == "feature_of":
        if feature_of_canonical_place_id is None:
            raise ValueError("feature_of references require their canonical primary")
    elif feature_of_canonical_place_id is not None:
        raise ValueError("only feature_of references may name a canonical parent")


def check_place_assessment_references(
    assessment: str, reference_ids: tuple[str, ...]
) -> None:
    """The consistency core for a sentence place assessment.

    Three rules: (1) a no-claim assessment carries no references; (2) a
    place-references assessment carries at least one; (3) reference identities
    are unique. ``artifact.SentencePlaceAssessment`` adds its sentence-hash
    binding beside this call.
    """
    if assessment == "no_material_place_claim" and reference_ids:
        raise ValueError("no-place-claim assessment cannot contain references")
    if assessment == "place_references" and not reference_ids:
        raise ValueError("place-reference assessment cannot be empty")
    if len(reference_ids) != len(set(reference_ids)):
        raise ValueError("sentence place assessment repeats a reference identity")


class CoordinateProvenance(BaseModel):
    """Exact authority and source payload behind one coordinate pair."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_kind: Literal[
        "corpus_record", "calibrated_provider", "human_authority"
    ]
    authority_id: str = Field(..., min_length=1)
    source_record_id: str = Field(..., min_length=1)
    coordinates: tuple[float, float]
    payload_json: str = Field(..., min_length=2)
    payload_sha256: str = Field(..., pattern=SHA256_PATTERN)

    _coordinates_are_valid = field_validator("coordinates")(valid_coordinates)

    @model_validator(mode="after")
    def _payload_is_exact(self) -> CoordinateProvenance:
        canonical_payload_bound_to_hash(
            self.payload_json,
            self.payload_sha256,
            label="coordinate provenance payload",
            require_object=True,
        )
        parsed = json.loads(self.payload_json)
        # Everything above is the shared canonical-and-bound rule. What follows is
        # this model's OWN extra requirement — that the payload agrees with the
        # fields beside it — and is genuinely not shared.
        if parsed.get("source_record_id") != self.source_record_id:
            raise ValueError("coordinate provenance source record differs from payload")
        if parsed.get("coordinates") != list(self.coordinates):
            raise ValueError("coordinate provenance coordinates differ from payload")
        return self

    @classmethod
    def build(
        cls,
        *,
        authority_kind: Literal[
            "corpus_record", "calibrated_provider", "human_authority"
        ],
        authority_id: str,
        source_record_id: str,
        coordinates: tuple[float, float],
        source_payload: dict[str, object] | None = None,
    ) -> CoordinateProvenance:
        payload = dict(source_payload or {})
        payload["source_record_id"] = source_record_id
        payload["coordinates"] = list(coordinates)
        payload_json = canonical_json(payload)
        return cls(
            authority_kind=authority_kind,
            authority_id=authority_id,
            source_record_id=source_record_id,
            coordinates=coordinates,
            payload_json=payload_json,
            payload_sha256=text_sha256(payload_json),
        )


__all__ = [
    "CoordinateProvenance",
    "bytes_sha256",
    "canonical_bytes",
    "canonical_json",
    "canonical_sha256",
    "check_place_assessment_references",
    "model_sha256",
    "strip_diacritics",
    "text_sha256",
]
