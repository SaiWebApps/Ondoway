"""Immutable blueprint and baked-asset contracts for tour certification.

Planning objects are mutable in meaning: routing can be rerun and narration can
be recomposed.  A certification artifact freezes the resolved request, exact
route, final served script, playback placement, and byte-bound audio evidence
under deterministic SHA-256 fingerprints.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from src.audio.tts_normalize import TTS_NORMALIZATION_VERSION, normalize_for_tts

from .contract import Route, Script, Sentence, TourInput
from .corpus_places import (
    canonical_payload_bound_to_hash,
    check_place_assessment_references,
    check_reference_lane,
    text_sha256,
    valid_coordinates,
)
from .generation import is_walk_concurrent

SHA256_PATTERN = r"^[0-9a-f]{64}$"
PROBE_VERSION = "ondoway-audio-probe-v1"
BLUEPRINT_SCHEMA_VERSION = "ondoway-tour-blueprint-v1"
ARTIFACT_SCHEMA_VERSION = "ondoway-tour-artifact-v1"

Placement = Literal["leg", "stop"]
ObservationAction = Literal[
    "look_at_feature",
    "scan_visible_scene",
    "compare_visible_features",
    "trace_visible_feature",
]


def _canonical_sha256(value: BaseModel) -> str:
    payload = json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sentences_payload_sha256(sentences: Iterable[Sentence]) -> str:
    payload = json.dumps(
        [sentence.model_dump(mode="json") for sentence in sentences],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class NarrationChunk(BaseModel):
    """A nonempty group of final sentences heard in one playback context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cue_id: str = Field(..., min_length=1)
    placement: Placement
    index: int = Field(..., ge=0)
    sentence_indexes: tuple[int, ...] = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    narration_sha256: str = Field(..., pattern=SHA256_PATTERN)
    tts_text: str = Field(..., min_length=1)
    tts_text_sha256: str = Field(..., pattern=SHA256_PATTERN)
    normalization_version: Literal["ondoway-tts-normalize-v1"]

    @model_validator(mode="after")
    def _hash_matches_text(self) -> NarrationChunk:
        if self.narration_sha256 != text_sha256(self.text):
            raise ValueError("narration_sha256 does not match chunk text")
        if self.tts_text != normalize_for_tts(self.text):
            raise ValueError("tts_text is not the canonical normalized narration")
        if self.tts_text_sha256 != text_sha256(self.tts_text):
            raise ValueError("tts_text_sha256 does not match normalized narration")
        if len(set(self.sentence_indexes)) != len(self.sentence_indexes):
            raise ValueError("a narration chunk repeats a sentence index")
        return self


class PlaybackAssignment(BaseModel):
    """Explicit final playback context for one exact sentence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sentence_index: int = Field(..., ge=0)
    placement: Placement
    index: int = Field(..., ge=0)


class ExplicitObservation(BaseModel):
    """A deliberate listener action with a stated duration and cue identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stop_index: int = Field(..., ge=0)
    sentence_index: int = Field(..., ge=0)
    sentence_sha256: str = Field(..., pattern=SHA256_PATTERN)
    instruction: str = Field(..., min_length=1)
    action: ObservationAction
    duration_seconds: float = Field(..., gt=0, le=60)
    cue_id: str = Field(..., min_length=1)
    cue_boundary: Literal["after_stop_audio"] = "after_stop_audio"

    @model_validator(mode="after")
    def _instruction_requires_a_spatial_action(self) -> ExplicitObservation:
        instruction = self.instruction.casefold()
        spatial_terms = {
            "look_at_feature": ("look", "notice", "find", "face", "turn"),
            "scan_visible_scene": ("look", "scan", "view", "take in"),
            "compare_visible_features": ("compare", "between", "both", "side by side"),
            "trace_visible_feature": ("trace", "follow", "along"),
        }
        if not any(term in instruction for term in spatial_terms[self.action]):
            raise ValueError("observation instruction does not express its spatial action")
        if any(term in instruction for term in ("wait", "pause", "reflect", "think about")):
            raise ValueError("generic waiting or reflection cannot count as an observation")
        return self


PlaceResolutionState = Literal["resolved", "unresolved", "ambiguous"]
PerceivabilityState = Literal["perceivable", "not_perceivable", "unresolved", "not_applicable"]
ExperientialPlaceRelation = Literal["primary", "feature_of", "off_site", "directional"]
DirectionalInstruction = Literal[
    "north",
    "northeast",
    "east",
    "southeast",
    "south",
    "southwest",
    "west",
    "northwest",
    "ahead",
    "right",
    "behind",
    "left",
    "unknown",
]
DirectionalReferenceFrame = Literal["cardinal", "incoming_path", "unsupported"]


class PlaceEvidenceProvenance(BaseModel):
    """Replayable source identity for place evidence.

    This contract records what authority supplied the evidence and its exact
    canonical payload.  The trusted coordinator must additionally enforce
    that ``authority_id`` is present in the frozen corpus manifest, calibrated
    provider set, or authorized human-review roster before it constructs a
    certifiable bundle; a self-asserted authority is not made trustworthy by a
    hash alone.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_kind: Literal["corpus_record", "calibrated_provider", "human_authority"]
    authority_id: str = Field(..., min_length=1)
    payload_json: str = Field(..., min_length=2)
    payload_sha256: str = Field(..., pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _payload_is_canonical_and_bound(self) -> PlaceEvidenceProvenance:
        canonical_payload_bound_to_hash(
            self.payload_json,
            self.payload_sha256,
            label="place provenance payload",
            require_object=True,
        )
        return self


class CanonicalPrimaryPlaceEvidence(BaseModel):
    """Canonical identity assigned to one exact routed stop."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stop_index: int = Field(..., ge=0)
    route_poi_id: str = Field(..., min_length=1)
    canonical_place_id: str = Field(..., min_length=1)
    coordinates: tuple[float, float]
    provenance: PlaceEvidenceProvenance
    resolution_state: PlaceResolutionState
    perceivability_state: Literal["perceivable", "not_perceivable", "unresolved"]

    _coordinates_are_valid = field_validator("coordinates")(valid_coordinates)


class PlaceReferenceEvidence(BaseModel):
    """One typed place reference nested under an exact final sentence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_id: str = Field(..., min_length=1)
    sentence_sha256: str = Field(..., pattern=SHA256_PATTERN)
    reference_kind: Literal["experiential", "contextual_historical"]
    relation: ExperientialPlaceRelation | None = None
    canonical_place_id: str | None = Field(default=None, min_length=1)
    coordinates: tuple[float, float] | None = None
    feature_of_canonical_place_id: str | None = Field(default=None, min_length=1)
    directional_instruction: DirectionalInstruction | None = None
    directional_reference_frame: DirectionalReferenceFrame | None = None
    directional_origin: Literal["routed_stop"] | None = None
    directional_playback_context: Literal["stationary_stop"] | None = None
    provenance: PlaceEvidenceProvenance
    resolution_state: PlaceResolutionState
    perceivability_state: PerceivabilityState

    @field_validator("coordinates")
    @classmethod
    def _optional_coordinates_are_valid(
        cls, value: tuple[float, float] | None
    ) -> tuple[float, float] | None:
        return None if value is None else valid_coordinates(value)

    @model_validator(mode="after")
    def _reference_lane_is_consistent(self) -> PlaceReferenceEvidence:
        # The shared lane core lives in corpus_places (dedup-review
        # 2026-08-07); the artifact layer's own extras — directional
        # semantics and exact coordinate identity — follow it.
        check_reference_lane(
            self.reference_kind,
            self.relation,
            self.feature_of_canonical_place_id,
            self.perceivability_state,
        )
        if self.reference_kind == "contextual_historical":
            if any(
                value is not None
                for value in (
                    self.directional_instruction,
                    self.directional_reference_frame,
                    self.directional_origin,
                    self.directional_playback_context,
                )
            ):
                raise ValueError(
                    "contextual historical references cannot carry directional semantics"
                )
            return self
        if self.canonical_place_id is None or self.coordinates is None:
            raise ValueError("experiential references require canonical identity and coordinates")
        directional_values = (
            self.directional_instruction,
            self.directional_reference_frame,
            self.directional_origin,
            self.directional_playback_context,
        )
        if self.relation == "directional":
            if any(value is None for value in directional_values):
                raise ValueError(
                    "directional references require instruction, frame, origin, and context"
                )
        elif any(value is not None for value in directional_values):
            raise ValueError("only directional references may carry directional semantics")
        return self


class SentencePlaceAssessment(BaseModel):
    """Complete place-claim classification for one exact final sentence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sentence_index: int = Field(..., ge=0)
    sentence_sha256: str = Field(..., pattern=SHA256_PATTERN)
    assessment: Literal["no_material_place_claim", "place_references"]
    assessment_provenance: PlaceEvidenceProvenance
    references: tuple[PlaceReferenceEvidence, ...] = ()

    @model_validator(mode="after")
    def _assessment_is_complete(self) -> SentencePlaceAssessment:
        # The shared three-rule core lives in corpus_places (dedup-review
        # 2026-08-07); the artifact layer's own guard — every reference bound
        # to THIS exact sentence — follows it.
        check_place_assessment_references(
            self.assessment,
            tuple(reference.reference_id for reference in self.references),
        )
        if any(reference.sentence_sha256 != self.sentence_sha256 for reference in self.references):
            raise ValueError("place reference is bound to a different sentence")
        return self


class PlaceEvidenceBundle(BaseModel):
    """Typed place semantics frozen into the blueprint before certification.

    This bundle can prove the mechanism against this evidence but cannot make its
    authorities trustworthy. Production authorization remains explicitly
    reserved for the trusted coordinator's manifest/calibration/human-authority allowlisting.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_scope: Literal["mechanism_only_m3c"] = "mechanism_only_m3c"
    primary_places: tuple[CanonicalPrimaryPlaceEvidence, ...]
    sentence_assessments: tuple[SentencePlaceAssessment, ...]


class DerivedNarrationEvidence(BaseModel):
    """Frozen provenance for one non-beat sentence in the final narration.

    The reviewer never infers authority from a glue label.  This binding says
    which immutable inputs actually licensed the sentence and whether the
    sentence must contain a fact-checked claim.  A later stage will populate corpus-field
    bindings while assembling the product artifact; route-only bindings can be
    derived here without trusting model output.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sentence_index: int = Field(..., ge=0)
    sentence_sha256: str = Field(..., pattern=SHA256_PATTERN)
    source_id: str = Field(..., min_length=1)
    claim_requirement: Literal["MATERIAL", "REVIEW_ALL", "NONPROPOSITIONAL"]
    evidence_kind: Literal[
        "route_snapshot",
        "corpus_field",
        "visited_claims",
        "fixed_template",
    ]
    payload_json: str = Field(..., min_length=2)
    payload_sha256: str = Field(..., pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _payload_is_canonical_and_bound(self) -> DerivedNarrationEvidence:
        canonical_payload_bound_to_hash(
            self.payload_json,
            self.payload_sha256,
            label="derived narration evidence",
        )
        return self


class CompositionTrace(BaseModel):
    """One compose response and the exact final stop content derived from it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit_id: str = Field(..., pattern=r"^stop:[0-9]+$")
    stop_index: int = Field(..., ge=0)
    request_sha256: str = Field(..., pattern=SHA256_PATTERN)
    response_sha256: str = Field(..., pattern=SHA256_PATTERN)
    derivation: Literal["provider_response"]
    authorized_source_sentences: tuple[Sentence, ...] = Field(..., min_length=1)
    source_sentences: tuple[Sentence, ...] = Field(..., min_length=1)
    source_payload_sha256: str = Field(..., pattern=SHA256_PATTERN)
    source_sentence_indexes: tuple[int, ...] = Field(..., min_length=1)
    sentence_indexes: tuple[int, ...] = Field(..., min_length=1)
    sentence_sha256s: tuple[str, ...] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _unit_and_sentences_are_parallel(self) -> CompositionTrace:
        if self.unit_id != f"stop:{self.stop_index}":
            raise ValueError("composition trace unit differs from stop index")
        if len(self.sentence_indexes) != len(self.sentence_sha256s):
            raise ValueError("composition trace sentence indexes and hashes differ")
        if len(self.sentence_indexes) != len(self.source_sentence_indexes):
            raise ValueError("composition trace final and source indexes differ")
        if len(set(self.sentence_indexes)) != len(self.sentence_indexes):
            raise ValueError("composition trace repeats a sentence index")
        authorized_stops = {sentence.stop_idx for sentence in self.authorized_source_sentences}
        if authorized_stops != {self.stop_index}:
            raise ValueError("composition trace authorized source spans another stop")
        if self.source_payload_sha256 != sentences_payload_sha256(self.source_sentences):
            raise ValueError("composition trace source payload hash is inconsistent")
        if any(
            index < 0 or index >= len(self.source_sentences)
            for index in self.source_sentence_indexes
        ):
            raise ValueError("composition trace cites an absent source sentence")
        if tuple(sorted(self.source_sentence_indexes)) != self.source_sentence_indexes:
            raise ValueError("composition trace source sentence order is not monotonic")
        if len(set(self.source_sentence_indexes)) != len(self.source_sentence_indexes):
            raise ValueError("composition trace repeats a source sentence index")
        return self


class BuildFingerprint(BaseModel):
    """Exact code/data/model/runtime versions required for offline replay."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commit_sha: str = Field(..., pattern=r"^[0-9a-f]{40}$")
    corpus_sha256: str = Field(..., pattern=SHA256_PATTERN)
    module_version: str = Field(..., min_length=1)
    prompt_sha256: str = Field(..., pattern=SHA256_PATTERN)
    compose_model: str = Field(..., min_length=1)
    policy_version: str = Field(..., min_length=1)
    routing_engine: Literal["valhalla"]
    routing_version: str = Field(..., min_length=1)
    routing_config_sha256: str = Field(..., pattern=SHA256_PATTERN)
    tts_provider: Literal["openai", "elevenlabs"]
    tts_model: str = Field(..., min_length=1)
    tts_voice: str = Field(..., min_length=1)
    audio_probe_version: Literal["ondoway-audio-probe-v1"] = PROBE_VERSION
    audio_probe_capability: Literal["structural_duration"] = "structural_duration"
    tts_normalization_version: Literal["ondoway-tts-normalize-v1"] = TTS_NORMALIZATION_VERSION


def partition_final_script(
    script: Script,
    route: Route,
    *,
    vignette_beat_ids: Iterable[str],
    playback_assignments: Iterable[PlaybackAssignment],
) -> tuple[NarrationChunk, ...]:
    """Assign every final sentence exactly once to its leg or stationary stop."""
    script_poi_ids = tuple(poi.id for poi in script.selected_pois)
    route_poi_ids = tuple(poi.id for poi in route.pois)
    if script_poi_ids != route_poi_ids:
        raise ValueError("script selected_pois do not match the exact routed POI order")
    if not route.pois:
        raise ValueError("a final tour blueprint must contain at least one POI")

    seated_beat_ids = {beat_id for poi in script.selected_pois for beat_id in poi.beat_ids}
    frozen_vignette_ids = frozenset(vignette_beat_ids)
    if seated_beat_ids & frozen_vignette_ids:
        raise ValueError("a beat cannot be both seated narration and a vignette")
    unknown_beat_sources = {
        cited_id
        for sentence in script.script
        if sentence.source_type == "beat"
        for cited_id in sentence.cited_beat_ids
        if cited_id not in seated_beat_ids and cited_id not in frozen_vignette_ids
    }
    if unknown_beat_sources:
        raise ValueError(
            "final beat sentences are absent from both seated and exact vignette plans: "
            + ", ".join(sorted(unknown_beat_sources))
        )
    for sentence in script.script:
        cited_ids = set(sentence.cited_beat_ids)
        if cited_ids & seated_beat_ids and cited_ids & frozen_vignette_ids:
            raise ValueError("one fused sentence cannot cross stop and leg playback contexts")

    assignments = tuple(playback_assignments)
    assignment_indexes = [assignment.sentence_index for assignment in assignments]
    if sorted(assignment_indexes) != list(range(len(script.script))):
        raise ValueError("playback assignments must cover every final sentence exactly once")
    assignment_by_sentence = {assignment.sentence_index: assignment for assignment in assignments}

    grouped: dict[tuple[Placement, int], list[tuple[int, str]]] = defaultdict(list)
    for sentence_index, sentence in enumerate(script.script):
        if sentence.stop_idx >= len(route.pois):
            raise ValueError(
                f"sentence {sentence_index} references absent stop {sentence.stop_idx}"
            )
        assignment = assignment_by_sentence[sentence_index]
        if assignment.index != sentence.stop_idx:
            raise ValueError("playback assignment index must match sentence stop_idx")
        if assignment.placement == "leg" and assignment.index >= len(route.transits):
            raise ValueError(f"sentence {sentence_index} references absent leg {assignment.index}")
        grouped[(assignment.placement, assignment.index)].append((sentence_index, sentence.text))

    chunks: list[NarrationChunk] = []
    for stop_index in range(len(route.pois)):
        for placement in ("leg", "stop"):
            sentences = grouped.get((placement, stop_index), [])
            if not sentences:
                continue
            text = " ".join(value.strip() for _, value in sentences if value.strip()).strip()
            if not text:
                raise ValueError(
                    f"{placement} {stop_index} has final sentences but empty voiced text"
                )
            chunks.append(
                NarrationChunk(
                    cue_id=f"{placement}:{stop_index}",
                    placement=placement,
                    index=stop_index,
                    sentence_indexes=tuple(index for index, _ in sentences),
                    text=text,
                    narration_sha256=text_sha256(text),
                    tts_text=normalize_for_tts(text),
                    tts_text_sha256=text_sha256(normalize_for_tts(text)),
                    normalization_version=TTS_NORMALIZATION_VERSION,
                )
            )

    covered = sorted(index for chunk in chunks for index in chunk.sentence_indexes)
    if covered != list(range(len(script.script))):
        raise ValueError("final narration partition omitted or duplicated sentences")
    return tuple(chunks)


def derive_playback_assignments(
    script: Script,
    *,
    vignette_beat_ids: Iterable[str],
    transit_beat_ids: Iterable[str] = (),
) -> tuple[PlaybackAssignment, ...]:
    """Emit and freeze today's explicit playback policy at generation time.

    ``transit_beat_ids``: the corpus transit beats ``_build_transit``
    voices FOR the leg — their sentences freeze as leg playback, so the phone
    plays the walk's own narration while walking, never standing at the stop.
    """
    frozen_vignette_ids = frozenset(vignette_beat_ids)
    frozen_transit_ids = frozenset(transit_beat_ids)
    seated_beat_ids = {beat_id for poi in script.selected_pois for beat_id in poi.beat_ids}
    assignments: list[PlaybackAssignment] = []
    for sentence_index, sentence in enumerate(script.script):
        cited_ids = set(sentence.cited_beat_ids)
        if cited_ids & seated_beat_ids and cited_ids & frozen_vignette_ids:
            raise ValueError("one fused sentence cannot cross stop and leg playback contexts")
        # THE one leg-vs-stop rule is generation.is_walk_concurrent — this used
        # to re-spell its glue-label set inline, so the freeze and the audio
        # clock could drift apart (dedup-review finding, 2026-08-07).
        placement: Placement = (
            "leg"
            if is_walk_concurrent(sentence, frozen_vignette_ids, frozen_transit_ids)
            else "stop"
        )
        assignments.append(
            PlaybackAssignment(
                sentence_index=sentence_index,
                placement=placement,
                index=sentence.stop_idx,
            )
        )
    return tuple(assignments)


def remap_provider_playback_assignments(
    *,
    source_script: Script,
    source_assignments: tuple[PlaybackAssignment, ...],
    provider_script: Script,
    derivable_leg_source_ids: frozenset[str] | None = None,
) -> tuple[PlaybackAssignment, ...]:
    """Carry frozen source placement onto exact provider-authored sentences.

    Placement is explicit plan data, not a decision inferred from a glue label or
    from final prose. A missing or ambiguous source placement fails closed.

    ``derivable_leg_source_ids`` is a NARROW, OPT-IN relaxation of the "missing"
    half, defaulting to None = fail closed exactly as before. When supplied, a
    provider sentence whose ``(source_id, stop_idx)`` pair is absent from the frozen
    source may still be placed -- but ONLY when its ``source_id`` is in that set (the
    concurrent glue labels plus this tour's vignette beat ids) or is a beat id the
    frozen source cites somewhere. Anything else, including an invented label, still
    raises.

    Why this is not "inferring placement from a glue label": ``derive_playback_assignments``
    computes placement as a PURE FUNCTION of ``source_id`` -- "leg" for a concurrent
    glue label or a vignette beat, "stop" otherwise -- and index as ``stop_idx``
    verbatim. So for a recognised source_id the derived value is identical to what the
    freeze would have produced had the composer emitted that sentence at plan time.
    Nothing is read from prose.

    Why it is opt-in: its ONLY caller is ``finalize_premium_tour``, which finalizes
    the tours ``POST /trips/{trip_id}/compose`` writes and keeps no receipts
    (EphemeralReceiptSink). The certification batch runner reaches
    ``finalize_premium_composition`` directly and never enters this function, so the
    fail-closed default keeps certification replay strict. The composer may add a
    transition sentence the frozen source lacks; without this relaxation such a tour
    is refused whole for that one sentence.
    """

    if tuple(item.sentence_index for item in source_assignments) != tuple(
        range(len(source_script.script))
    ):
        raise ValueError("source playback assignments must cover every source sentence")

    placement_by_source: dict[tuple[str, int], set[tuple[Placement, int]]] = {}
    for assignment, sentence in zip(source_assignments, source_script.script, strict=True):
        if assignment.index != sentence.stop_idx:
            raise ValueError("source playback assignment differs from its sentence stop")
        placement_by_source.setdefault((sentence.source_id, sentence.stop_idx), set()).add(
            (assignment.placement, assignment.index)
        )

    result: list[PlaybackAssignment] = []
    for sentence_index, sentence in enumerate(provider_script.script):
        placements = placement_by_source.get((sentence.source_id, sentence.stop_idx))
        if placements is None and derivable_leg_source_ids is not None:
            # Recognised provenance at a position the freeze did not contain. Derive
            # with derive_playback_assignments' EXACT rule (pure function of
            # source_id), never from prose. An id the frozen source never cites at
            # all is NOT recognised and falls through to the raise below.
            cited_source_ids = {s.source_id for s in source_script.script}
            if sentence.source_id in derivable_leg_source_ids or (
                sentence.source_type == "beat" and sentence.source_id in cited_source_ids
            ):
                # Same ONE rule as the generation-time freeze (the only caller
                # passes CONCURRENT_GLUE_LABELS | vignette ids, so the answers
                # are identical by construction, not by parallel maintenance).
                placements = {
                    (
                        "leg"
                        if is_walk_concurrent(sentence, derivable_leg_source_ids)
                        else "stop",
                        sentence.stop_idx,
                    )
                }
        if placements is None:
            # NAME THE OFFENDER. This fails an entire authored tour, and the bare
            # message made the cause unrecoverable: the reader cannot tell an added
            # glue sentence from a beat cited at the wrong stop, and the provider
            # script is not persisted. The pair is provenance metadata (a glue label
            # or a beat id) and a stop index -- not narration prose -- so it is safe
            # to name here; callers still must not put it on the wire.
            raise ValueError(
                "provider sentence cites no frozen playback source "
                f"(source_id={sentence.source_id!r}, stop_idx={sentence.stop_idx}, "
                f"source_type={sentence.source_type!r}); the frozen source has no "
                f"sentence with that (source_id, stop_idx) pair"
            )
        if len(placements) != 1:
            raise ValueError(
                "provider sentence source has ambiguous frozen playback "
                f"(source_id={sentence.source_id!r}, stop_idx={sentence.stop_idx}, "
                f"candidates={sorted(placements)})"
            )
        placement, index = next(iter(placements))
        if index != sentence.stop_idx:
            raise ValueError(
                "provider sentence moved its source to another playback index "
                f"(source_id={sentence.source_id!r}, frozen_index={index}, "
                f"sentence_stop_idx={sentence.stop_idx})"
            )
        result.append(
            PlaybackAssignment(
                sentence_index=sentence_index,
                placement=placement,
                index=index,
            )
        )
    return tuple(result)


class FinalTourBlueprint(BaseModel):
    """Everything fixed before TTS is allowed to bake final assets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ondoway-tour-blueprint-v1"] = BLUEPRINT_SCHEMA_VERSION
    contract_sha256: str = Field(..., pattern=SHA256_PATTERN)
    reference_manifest_sha256: str = Field(..., pattern=SHA256_PATTERN)
    calibration_manifest_sha256: str = Field(..., pattern=SHA256_PATTERN)
    build: BuildFingerprint
    tour_input: TourInput
    route: Route
    script: Script
    vignette_beat_ids: tuple[str, ...]
    playback_assignments: tuple[PlaybackAssignment, ...]
    narration_chunks: tuple[NarrationChunk, ...]
    observations: tuple[ExplicitObservation, ...] = ()
    derived_narration_evidence: tuple[DerivedNarrationEvidence, ...]
    composition_trace: tuple[CompositionTrace, ...] = ()
    place_evidence: PlaceEvidenceBundle | None = None

    @model_validator(mode="after")
    def _content_is_self_consistent(self) -> FinalTourBlueprint:
        if self.script.inputs != self.tour_input:
            raise ValueError("script inputs do not match blueprint tour_input")
        receipt_config_hashes = {
            transit.valhalla_receipt.routing_config_sha256
            for transit in self.route.transits
            if transit.valhalla_receipt is not None
        }
        if receipt_config_hashes and receipt_config_hashes != {self.build.routing_config_sha256}:
            raise ValueError(
                "Valhalla receipt routing configuration differs from build fingerprint"
            )
        if len(set(self.vignette_beat_ids)) != len(self.vignette_beat_ids):
            raise ValueError("vignette_beat_ids contains duplicates")
        expected = partition_final_script(
            self.script,
            self.route,
            vignette_beat_ids=self.vignette_beat_ids,
            playback_assignments=self.playback_assignments,
        )
        if self.narration_chunks != expected:
            raise ValueError("narration chunks do not match the exact final script")
        expected_derived = {
            index
            for index, sentence in enumerate(self.script.script)
            if sentence.source_type in {"glue", "arith"}
        }
        actual_derived = [item.sentence_index for item in self.derived_narration_evidence]
        if len(actual_derived) != len(set(actual_derived)):
            raise ValueError("derived narration evidence repeats a sentence")
        if set(actual_derived) != expected_derived:
            raise ValueError(
                "derived narration evidence must bind every non-beat sentence exactly once"
            )
        for item in self.derived_narration_evidence:
            sentence = self.script.script[item.sentence_index]
            if (
                item.sentence_sha256 != text_sha256(sentence.text)
                or item.source_id != sentence.source_id
            ):
                raise ValueError("derived narration evidence does not match final narration")
            if item.claim_requirement != "REVIEW_ALL":
                raise ValueError(
                    "derived narration claim requirement differs from the fixed policy"
                )
            if item.evidence_kind != "route_snapshot":
                raise ValueError(
                    "corpus-backed derived narration evidence is unsupported until a later stage "
                    "can bind it to verified source identities"
                )
            expected_payload = _route_evidence_payload(
                route=self.route,
                script=self.script,
                sentence_index=item.sentence_index,
            )
            if item.payload_json != expected_payload:
                raise ValueError(
                    "route narration evidence differs from the canonical route snapshot"
                )
        if self.composition_trace:
            trace_units = [item.unit_id for item in self.composition_trace]
            if len(trace_units) != len(set(trace_units)):
                raise ValueError("composition trace repeats a stop unit")
            traced_stops = sorted(item.stop_index for item in self.composition_trace)
            if traced_stops != list(range(len(self.route.pois))):
                raise ValueError("composition trace must cover every routed stop exactly once")
            traced_indexes = [
                index for item in self.composition_trace for index in item.sentence_indexes
            ]
            if sorted(traced_indexes) != list(range(len(self.script.script))):
                raise ValueError("composition trace must bind every final sentence exactly once")
            for item in self.composition_trace:
                expected_indexes = tuple(
                    index
                    for index, sentence in enumerate(self.script.script)
                    if sentence.stop_idx == item.stop_index
                )
                if item.sentence_indexes != expected_indexes:
                    raise ValueError(
                        "composition trace sentence indexes differ from the final stop"
                    )
                expected_hashes = tuple(
                    text_sha256(self.script.script[index].text) for index in expected_indexes
                )
                if item.sentence_sha256s != expected_hashes:
                    raise ValueError(
                        "composition trace sentence hashes differ from final narration"
                    )
                traced_sentences = tuple(
                    item.source_sentences[source_index]
                    for source_index in item.source_sentence_indexes
                )
                final_sentences = tuple(self.script.script[index] for index in expected_indexes)
                if traced_sentences != final_sentences:
                    raise ValueError("final narration is not the exact traced provider content")
        stationary_chunk_by_sentence = {
            sentence_index: chunk
            for chunk in self.narration_chunks
            if chunk.placement == "stop"
            for sentence_index in chunk.sentence_indexes
        }
        seen_observation_sentences: set[int] = set()
        for observation in self.observations:
            if observation.sentence_index in seen_observation_sentences:
                raise ValueError("two observations reference the same final sentence")
            seen_observation_sentences.add(observation.sentence_index)
            chunk = stationary_chunk_by_sentence.get(observation.sentence_index)
            if chunk is None:
                raise ValueError("observation must bind to a stationary final sentence")
            if observation.sentence_index != chunk.sentence_indexes[-1]:
                raise ValueError(
                    "observation must follow the final sentence of its stationary chunk"
                )
            sentence = self.script.script[observation.sentence_index]
            if sentence.stop_idx != observation.stop_index:
                raise ValueError("observation stop does not match its final sentence")
            if text_sha256(sentence.text) != observation.sentence_sha256:
                raise ValueError("observation sentence hash does not match final narration")
            if observation.instruction != sentence.text:
                raise ValueError("observation instruction differs from final narration")
        if self.place_evidence is not None:
            for primary in self.place_evidence.primary_places:
                if primary.stop_index >= len(self.route.pois):
                    raise ValueError("primary place references an absent route stop")
                route_poi = self.route.pois[primary.stop_index]
                if primary.route_poi_id != route_poi.id or primary.coordinates != (
                    route_poi.lat,
                    route_poi.lng,
                ):
                    raise ValueError("primary place does not bind its exact route stop")
            for assessment in self.place_evidence.sentence_assessments:
                if assessment.sentence_index >= len(self.script.script):
                    raise ValueError("place assessment references an absent sentence")
                sentence = self.script.script[assessment.sentence_index]
                if assessment.sentence_sha256 != text_sha256(sentence.text):
                    raise ValueError(
                        "place assessment sentence hash does not match final narration"
                    )
        return self

    def fingerprint(self) -> str:
        return _canonical_sha256(self)


class BakedAudioAsset(BaseModel):
    """Strict probe evidence for bytes baked from exactly one narration chunk."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_id: str = Field(..., min_length=1)
    audio_url: str = Field(..., min_length=1)
    blueprint_sha256: str = Field(..., pattern=SHA256_PATTERN)
    cue_id: str = Field(..., min_length=1)
    placement: Placement
    index: int = Field(..., ge=0)
    narration_sha256: str = Field(..., pattern=SHA256_PATTERN)
    tts_text_sha256: str = Field(..., pattern=SHA256_PATTERN)
    normalization_version: Literal["ondoway-tts-normalize-v1"]
    audio_sha256: str = Field(..., pattern=SHA256_PATTERN)
    size_bytes: int = Field(..., gt=0)
    duration_seconds: float = Field(..., gt=0)
    duration_source: Literal["media_probe"]
    probe_version: Literal["ondoway-audio-probe-v1"]
    probe_capability: Literal["structural_duration"]
    media_container: Literal["wav", "mp3"]
    media_codec: Literal["pcm", "mp3_layer_iii"]
    sample_rate_hz: int = Field(..., gt=0)
    channels: int = Field(..., gt=0)
    frame_count: int = Field(..., gt=0)
    provider: str = Field(..., min_length=1)
    tts_model: str = Field(..., min_length=1)
    tts_voice: str = Field(..., min_length=1)
    storage: str = Field(..., min_length=1)


class TourArtifact(BaseModel):
    """One immutable candidate evaluated by every certification gate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ondoway-tour-artifact-v1"] = ARTIFACT_SCHEMA_VERSION
    blueprint: FinalTourBlueprint
    audio_assets: tuple[BakedAudioAsset, ...]

    def fingerprint(self) -> str:
        return _canonical_sha256(self)


def build_final_blueprint(
    *,
    contract_sha256: str,
    reference_manifest_sha256: str,
    calibration_manifest_sha256: str,
    build: BuildFingerprint,
    tour_input: TourInput,
    route: Route,
    script: Script,
    vignette_beat_ids: Iterable[str],
    playback_assignments: Iterable[PlaybackAssignment],
    observations: Iterable[ExplicitObservation] = (),
    derived_narration_evidence: Iterable[DerivedNarrationEvidence] | None = None,
    composition_trace: Iterable[CompositionTrace] = (),
    place_evidence: PlaceEvidenceBundle | None = None,
) -> FinalTourBlueprint:
    """Freeze the exact post-compose input to the audio and gate pipeline."""
    frozen_vignette_ids = tuple(vignette_beat_ids)
    frozen_assignments = tuple(playback_assignments)
    chunks = partition_final_script(
        script,
        route,
        vignette_beat_ids=frozen_vignette_ids,
        playback_assignments=frozen_assignments,
    )
    frozen_derived = (
        tuple(derived_narration_evidence)
        if derived_narration_evidence is not None
        else _route_narration_evidence(route=route, script=script)
    )
    return FinalTourBlueprint(
        contract_sha256=contract_sha256,
        reference_manifest_sha256=reference_manifest_sha256,
        calibration_manifest_sha256=calibration_manifest_sha256,
        build=build,
        tour_input=tour_input,
        route=route,
        script=script,
        vignette_beat_ids=frozen_vignette_ids,
        playback_assignments=frozen_assignments,
        narration_chunks=chunks,
        observations=tuple(observations),
        derived_narration_evidence=frozen_derived,
        composition_trace=tuple(composition_trace),
        place_evidence=place_evidence,
    )


def _route_narration_evidence(
    *, route: Route, script: Script
) -> tuple[DerivedNarrationEvidence, ...]:
    """Build the narrow route evidence available without the source corpus.

    Request prose, theme hints, and pre-bake script estimates are deliberately
    absent.  Corpus cues, pronunciations, and reflection claims must be supplied
    explicitly by the caller instead of being silently blessed by this fallback seam.
    """
    evidence: list[DerivedNarrationEvidence] = []
    for sentence_index, sentence in enumerate(script.script):
        if sentence.source_type == "beat":
            continue
        canonical = _route_evidence_payload(
            route=route, script=script, sentence_index=sentence_index
        )
        evidence.append(
            DerivedNarrationEvidence(
                sentence_index=sentence_index,
                sentence_sha256=text_sha256(sentence.text),
                source_id=sentence.source_id,
                claim_requirement="REVIEW_ALL",
                evidence_kind="route_snapshot",
                payload_json=canonical,
                payload_sha256=text_sha256(canonical),
            )
        )
    return tuple(evidence)


def _route_evidence_payload(*, route: Route, script: Script, sentence_index: int) -> str:
    sentence = script.script[sentence_index]
    stop_index = sentence.stop_idx
    stop = route.pois[stop_index] if stop_index < len(route.pois) else None
    previous = route.pois[stop_index - 1] if 0 < stop_index <= len(route.pois) else None
    transit = route.transits[stop_index] if stop_index < len(route.transits) else None
    payload = {
        "destination_stop": stop.model_dump(mode="json") if stop else None,
        "incoming_transit": transit.model_dump(mode="json") if transit else None,
        "previous_stop": previous.model_dump(mode="json") if previous else None,
        "route": {
            "routed": route.routed,
            "spine_area": route.spine_area,
            "total_walk_distance_m": route.total_walk_distance_m,
            "total_walk_seconds": route.total_walk_seconds,
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def validate_blueprint_integrity(blueprint: FinalTourBlueprint) -> str | None:
    """Revalidate serialized content; never trust constructor history/model_copy."""
    try:
        FinalTourBlueprint.model_validate(blueprint.model_dump(mode="python"))
    except (ValidationError, ValueError) as exc:
        return str(exc)
    return None


def validate_llm_composed_blueprint(blueprint: FinalTourBlueprint) -> str | None:
    """Return why a blueprint is ineligible for quality certification.

    A deterministic ``Script`` remains a valid Basic Tour, but it is not an
    LLM-composed certification candidate.  Certification requires a non-empty
    physical provider trace covering the final narration.  Revalidating first
    prevents ``model_copy`` or deserialization tricks from reviving the retired
    stitched-fallback derivation.
    """
    integrity_error = validate_blueprint_integrity(blueprint)
    if integrity_error:
        return f"invalid final blueprint: {integrity_error}"
    if not blueprint.composition_trace:
        return "basic tour has no LLM provider composition trace"
    if any(trace.derivation != "provider_response" for trace in blueprint.composition_trace):
        return "final narration is not derived exclusively from LLM provider responses"
    return None


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "BLUEPRINT_SCHEMA_VERSION",
    "BakedAudioAsset",
    "BuildFingerprint",
    "CanonicalPrimaryPlaceEvidence",
    "CompositionTrace",
    "DerivedNarrationEvidence",
    "ExplicitObservation",
    "FinalTourBlueprint",
    "NarrationChunk",
    "ObservationAction",
    "PlaceEvidenceBundle",
    "PlaceEvidenceProvenance",
    "PlaceReferenceEvidence",
    "PlaybackAssignment",
    "SentencePlaceAssessment",
    "TourArtifact",
    "build_final_blueprint",
    "derive_playback_assignments",
    "partition_final_script",
    "remap_provider_playback_assignments",
    "sentences_payload_sha256",
    "validate_blueprint_integrity",
    "validate_llm_composed_blueprint",
]
