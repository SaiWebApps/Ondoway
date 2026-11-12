"""Canonical physical request envelopes for bounded quality-provider calls.

Both the adapters and terminal replay use these builders.  A request hash therefore
binds the model, token ceiling, policy, task, JSON schema, messages, and exact typed
input instead of merely proving that some response happened.
"""

from __future__ import annotations

import json
from typing import Literal

import anthropic
from pydantic import BaseModel, ConfigDict, Field

from .corpus_places import text_sha256
from .quality_certification import (
    ENJOY_AXIS_NAMES,
    EnjoymentAxisAssessment,
    EnjoymentAxisScores,
    EnjoymentCalibrationAnchor,
    EnjoymentEvidenceSpan,
    EnjoymentItem,
    EnjoymentScoredAssessment,
    FactFinding,
    FactPolicyCalibrationCase,
    FactPolicyCalibrationVerdict,
    FactReviewInput,
    FactSentenceVerdict,
    QualityAdjudicationInput,
    resolve_enjoyment_evidence_offsets,
)

FACT_MAX_OUTPUT_TOKENS = 64_000
ENJOY_MAX_OUTPUT_TOKENS = 16_000
MAX_REVIEW_INPUT_BYTES = 750_000

#: Sonnet 5 tokenizes ~30% denser than Opus and its adaptive thinking spends more
#: against the same ceiling: the first live Sonnet calibration burned the full 16K
#: and truncated mid-JSON (stop_reason max_tokens, 2026-08-28) on the exact request
#: Opus finished in 6,363 output tokens. Under zero retries a max_tokens stop is
#: paid-and-lost, so dense models get double the ceiling — a worst-case bound,
#: never expected spend (billing is actual tokens).
_DENSE_OUTPUT_MODELS = ("claude-sonnet-5",)


def _output_ceiling(model: str, base: int) -> int:
    return base * 2 if model in _DENSE_OUTPUT_MODELS else base
QUALITY_SCHEMA_TRANSFORMER_ID = "anthropic-python-transform-schema-0.97.0"
_PINNED_ANTHROPIC_SDK_VERSION = "0.97.0"

SHARED_POLICY = """\
You are certifying a spoken walking tour under a frozen policy. Do not score it.
Do not invent criteria. Provider, parse, or schema trouble is never evidence that a
tour is bad.

FACT policy: check material assertions about entities/roles, attribution, dates/eras/
numbers, place/direction, specific events, historical cause or agency, objective
rankings, quotations, and certainty/dispute status. Natural paraphrase, compression,
clause reordering, and non-propositional colour are allowed only when they change no
underlying checkable entity, act, event, cause, date, place, attribution, quotation,
ranking, or certainty. Exact calibration cases test this boundary without revealing
their human labels to the reviewer.
Treat ordinary synonyms as factually equivalent when they preserve the same checkable
proposition; factual support does not require word-for-word entailment.
Do not infer a new manner, intent, actor, cause, or mechanism solely from an ordinary
paraphrase's possible connotations. A FACT failure requires language that explicitly
changes or supplies an unsupported checkable proposition; a merely possible reading
is not a factual defect.
Treat active-voice and passive-voice renderings as equivalent only when they preserve
the same actor, action, and affected entity without adding or removing agency.
Allow compression that omits nonessential detail when the retained proposition remains
supported and the omission changes no scope, certainty, attribution, or causation.
Allow clause reordering when every proposition and their logical relationship remain
unchanged; adjacency must not create a new cause, sequence, or attribution.
Editorial preference about how a supported proposition is expressed is outside FACT;
a FACT failure requires a changed or unsupported checkable proposition, not merely a
preference for different wording.
Account for every sentence and every atomic checkable claim or presupposition. Never
silently emit zero claims for factual prose. A source union does not license a new
relationship merely because both entities occur somewhere in it. Quotes are exact.

ENJOY policy: assess the listening experience on five named craft axes. Each axis is
scored 0, 1, or 2 and requires exact evidence from the item being assessed.

site_presence: 2 when the narration strongly uses the actual site, surroundings,
observable details, or listener actions; 1 when that connection is occasional or
uneven; 0 when the narration is substantially disconnected from being there.
narrative_motion: 2 when the material develops through setup, discovery, consequence,
reveal, or payoff; 1 when there is some movement but it is local or uneven; 0 when the
material remains a static recital without meaningful progression.
human_specificity: 2 when people, motives, choices, stakes, or telling particulars make
the story vivid; 1 when human detail exists but remains thin; 0 when the material stays
predominantly abstract or institutional.
spoken_voice: 2 when it sounds natural aloud, addresses the listener clearly, and uses
varied, controlled rhythm; 1 when it is understandable but intermittently stiff; 0
when it is persistently unsuitable as spoken guidance.
editorial_control: 2 when selection, pacing, emphasis, and transitions feel intentional;
1 when the material is usable but includes noticeable clutter or imbalance; 0 when it
is substantially uncurated, confusing, redundant, or overloaded.

Do not produce a total, release decision, PASS/FAIL judgment, candidate label, gold
label, or role inference. The server computes all totals and release decisions. Opaque
items are presented in a neutral collection and must be assessed independently.
"""

FACT_TASK = """\
Review the complete JSON FACT batch below. Evidence is authorized sentence-by-sentence;
never cite evidence from another sentence. Echo every sentence index and hash exactly.
Partition each sentence into contiguous exact UTF-8 byte spans covering all text, then
classify every span as MATERIAL_CLAIM, LICENSED_GLOSS, or CONNECTIVE. For material
claims, record the supported, repairable-narrowing, or material-defect decision,
material type, and authorized evidence ids. PASS permits only supported/connective
claims; LICENSED_COLOR requires licensed nonpropositional gloss; FAIL requires a
matching exact finding for every confirmed material defect. Explain genuinely
fact-free sentences with no_material_assertion_reason. Return only schema-constrained
JSON.
"""

BATCH_FACT_TASK = """\
Review the complete JSON FACT batch below. Evidence is authorized sentence-by-sentence;
never cite evidence from another sentence. Echo every sentence index and hash exactly.
Analyze every material claim and factual presupposition, but return a compact verdict:
PASS, LICENSED_COLOR, or FAIL for every sentence. PASS and LICENSED_COLOR carry no
findings. FAIL must include each confirmed material defect with its exact UTF-8 passage
offsets, authorized evidence ids, material type, severity, and concise explanation.
Do not emit full-sentence span partitions or supported-claim inventories.
Use REPAIRABLE_NARROWING only when a
local deletion/narrowing can retain a supported proposition and narrative role. Use
MATERIAL_DEFECT for contradictions, misattributions, altered quotes, lost uncertainty,
broken entities, unsupported relations, or defects not safely narrowable. A genuinely
fact-free sentence needs an explicit no_material_assertion_reason. Return only the
schema-constrained JSON.
"""

ENJOY_TASK = """\
Assess every opaque item independently on all five ENJOY axes. For each axis, choose
0, 1, or 2 using the frozen axis definition and cite at least one exact, non-empty
substring from that same item's narration. Explain how the cited passage supports the
score. Select evidence by segment_id and exact_passage; the server derives and verifies
UTF-8 offsets deterministically. Emit each
supplied item exactly once. Do not compare named roles, infer which
item is a candidate or baseline, compute totals, or issue a release decision. Return
only schema-constrained JSON.
"""

CALIBRATION_TASK = """\
This input contains only frozen calibration cases and anchor texts; no candidate is
present. Classify every FACT policy case PASS or FAIL under the exact licensed-colour
boundary. Assess every ENJOY item independently on all five 0-2 axes, with exact
same-item evidence for every axis. Select evidence by segment_id and exact_passage; the
server derives and verifies UTF-8 offsets deterministically. Emit every case/item
exactly once. Human labels are
intentionally absent. Do not compute ENJOY totals or decisions. Return only the
schema-constrained JSON.
"""

ADJUDICATION_TASK = """\
Independently review only the candidate sections present in this input. You are blind:
the primary reviewers' verdicts and explanations are intentionally absent. FACT and
ENJOY outputs are separate; never infer one from the other. Emit every requested FACT
sentence and ENJOY item exactly once. Return only the schema-constrained JSON.
"""


class ProviderFactSentenceVerdict(BaseModel):
    """Compact physical FACT result; adaptive reasoning need not be retransmitted."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    sentence_index: int = Field(..., ge=0)
    sentence_sha256: str
    decision: Literal["PASS", "LICENSED_COLOR", "FAIL"]
    findings: tuple[FactFinding, ...] = ()


class FactPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdicts: tuple[FactSentenceVerdict, ...]


class ProviderFactPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", title="FactPayload")

    verdicts: tuple[ProviderFactSentenceVerdict, ...]


class ProviderEnjoymentEvidence(BaseModel):
    """Provider-selected quotation; byte offsets are deliberately server-owned."""

    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(..., min_length=1)
    exact_passage: str = Field(..., min_length=1)


class ProviderEnjoymentAxisAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: Literal[0, 1, 2]
    evidence: tuple[ProviderEnjoymentEvidence, ...] = Field(..., min_length=1)
    explanation: str = Field(..., min_length=1)


class ProviderEnjoymentAxisScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_presence: ProviderEnjoymentAxisAssessment
    narrative_motion: ProviderEnjoymentAxisAssessment
    human_specificity: ProviderEnjoymentAxisAssessment
    spoken_voice: ProviderEnjoymentAxisAssessment
    editorial_control: ProviderEnjoymentAxisAssessment


class ProviderEnjoymentScoredAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(..., min_length=1)
    axes: ProviderEnjoymentAxisScores


class ProviderEnjoyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessments: tuple[ProviderEnjoymentScoredAssessment, ...]


class ProviderCalibrationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_verdicts: tuple[FactPolicyCalibrationVerdict, ...]
    enjoyment_assessments: tuple[ProviderEnjoymentScoredAssessment, ...]


class ProviderAdjudicationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_verdicts: tuple[FactSentenceVerdict, ...] = ()
    enjoyment_assessments: tuple[ProviderEnjoymentScoredAssessment, ...] = ()


def materialize_provider_enjoyment_assessments(
    assessments: tuple[ProviderEnjoymentScoredAssessment, ...],
    *,
    items: tuple[EnjoymentItem, ...],
) -> tuple[EnjoymentScoredAssessment, ...]:
    """Convert provider quotation selections to offset-bound internal evidence."""
    internal: list[EnjoymentScoredAssessment] = []
    for assessment in assessments:
        axes: dict[str, EnjoymentAxisAssessment] = {}
        for axis_name in ENJOY_AXIS_NAMES:
            axis = getattr(assessment.axes, axis_name)
            axes[axis_name] = EnjoymentAxisAssessment(
                score=axis.score,
                evidence=tuple(
                    EnjoymentEvidenceSpan(
                        segment_id=evidence.segment_id,
                        passage_byte_start=0,
                        passage_byte_end=1,
                        exact_passage=evidence.exact_passage,
                    )
                    for evidence in axis.evidence
                ),
                explanation=axis.explanation,
            )
        internal.append(
            EnjoymentScoredAssessment(
                item_id=assessment.item_id,
                axes=EnjoymentAxisScores.model_validate(axes),
            )
        )
    return resolve_enjoyment_evidence_offsets(tuple(internal), items=items)


class EnjoyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessments: tuple[EnjoymentScoredAssessment, ...]


class CalibrationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_verdicts: tuple[FactPolicyCalibrationVerdict, ...]
    enjoyment_assessments: tuple[EnjoymentScoredAssessment, ...]


class AdjudicationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_verdicts: tuple[FactSentenceVerdict, ...] = ()
    enjoyment_assessments: tuple[EnjoymentScoredAssessment, ...] = ()


def _provider_schema(model: type[BaseModel]) -> dict[str, object]:
    """Compile Pydantic constraints to Anthropic's supported JSON Schema subset.

    The transformer version is pinned because its output is part of every physical
    request hash.  Upgrading the SDK therefore requires a conscious re-authorization
    instead of silently changing a paid request after its ledger is frozen.
    """
    if anthropic.__version__ != _PINNED_ANTHROPIC_SDK_VERSION:
        raise RuntimeError(
            "quality schema transformer version changed; rebuild and re-authorize "
            "the certification request policy"
        )
    schema = anthropic.transform_schema(model)
    description = str(schema.get("description", "")).strip()
    transformer_note = f"Schema compiler: {QUALITY_SCHEMA_TRANSFORMER_ID}."
    schema["description"] = (
        f"{description}\n\n{transformer_note}" if description else transformer_note
    )
    return schema


FACT_PROMPT_SHA256 = text_sha256(SHARED_POLICY + FACT_TASK)
BATCH_FACT_PROMPT_SHA256 = text_sha256(SHARED_POLICY + BATCH_FACT_TASK)
ENJOY_PROMPT_SHA256 = text_sha256(SHARED_POLICY + ENJOY_TASK)
QUALITY_POLICY_SHA256 = text_sha256(SHARED_POLICY)
CALIBRATION_PROMPT_SHA256 = text_sha256(SHARED_POLICY + CALIBRATION_TASK)
ADJUDICATION_PROMPT_SHA256 = text_sha256(
    SHARED_POLICY + FACT_TASK + ENJOY_TASK + ADJUDICATION_TASK
)


def _request_envelope(
    *,
    model: str,
    max_tokens: int,
    system: str,
    schema: dict[str, object],
    user: str,
) -> tuple[str, dict[str, object]]:
    sdk_request: dict[str, object] = {
        "model": model,
        "max_tokens": max_tokens,
        "thinking": {"type": "adaptive"},
        "system": system,
        "output_config": {
            "format": {"type": "json_schema", "schema": schema}
        },
        "messages": [{"role": "user", "content": user}],
    }
    return (
        json.dumps(
            sdk_request,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        sdk_request,
    )


def fact_request_envelope(
    request: FactReviewInput, *, model: str
) -> tuple[str, dict[str, object]]:
    user = FACT_TASK + "\n\nFACT BATCH:\n" + request.model_dump_json()
    return _request_envelope(
        model=model,
        max_tokens=FACT_MAX_OUTPUT_TOKENS,
        system=SHARED_POLICY,
        schema=_provider_schema(FactPayload),
        user=user,
    )


def batch_fact_request_envelope(
    request: FactReviewInput, *, model: str
) -> tuple[str, dict[str, object]]:
    user = BATCH_FACT_TASK + "\n\nFACT BATCH:\n" + request.model_dump_json()
    return _request_envelope(
        model=model,
        max_tokens=FACT_MAX_OUTPUT_TOKENS,
        system=SHARED_POLICY,
        schema=_provider_schema(ProviderFactPayload),
        user=user,
    )


def calibration_request_envelope(
    fact_cases: tuple[FactPolicyCalibrationCase, ...],
    enjoyment_anchors: tuple[EnjoymentCalibrationAnchor, ...],
    *,
    model: str,
) -> tuple[str, dict[str, object]]:
    payload = {
        "fact_policy_cases": [
            {
                "case_id": case.case_id,
                "source_text": case.source_text,
                "candidate_text": case.candidate_text,
            }
            for case in fact_cases
        ],
        "enjoyment_items": [
            item.model_dump(mode="json")
            for item in sorted(
                (anchor.item for anchor in enjoyment_anchors),
                key=lambda item: item.item_id,
            )
        ],
    }
    user = CALIBRATION_TASK + "\n\nCALIBRATION INPUT:\n" + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return _request_envelope(
        model=model,
        max_tokens=_output_ceiling(model, ENJOY_MAX_OUTPUT_TOKENS),
        system=SHARED_POLICY,
        schema=_provider_schema(ProviderCalibrationPayload),
        user=user,
    )


def enjoyment_request_envelope(
    items: tuple[EnjoymentItem, ...],
    anchors: tuple[EnjoymentCalibrationAnchor, ...],
    *,
    model: str,
) -> tuple[str, dict[str, object]]:
    neutral_items = tuple(sorted(
        (*tuple(anchor.item for anchor in anchors), *items),
        key=lambda item: item.item_id,
    ))
    if len({item.item_id for item in neutral_items}) != len(neutral_items):
        raise ValueError("blind enjoyment request repeats an opaque item id")
    payload = {
        "items": [item.model_dump(mode="json") for item in neutral_items],
    }
    user = ENJOY_TASK + "\n\nREVIEW INPUT:\n" + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    # Output scales with the CANDIDATE count: the blind meet-or-beat rides two
    # opaque days (old + new) through one request, and each assessed item costs
    # its own five axes of evidence. One candidate keeps the historical 16K
    # envelope byte-identical.
    return _request_envelope(
        model=model,
        max_tokens=_output_ceiling(model, ENJOY_MAX_OUTPUT_TOKENS * max(1, len(items))),
        system=SHARED_POLICY,
        schema=_provider_schema(ProviderEnjoyPayload),
        user=user,
    )


def adjudication_request_envelope(
    request: QualityAdjudicationInput, *, model: str
) -> tuple[str, dict[str, object]]:
    provider_input = {
        "blueprint_sha256": request.blueprint_sha256,
        "fact_request": request.fact_request.model_dump(mode="json"),
        "enjoyment_items": [
            item.model_dump(mode="json")
            for item in sorted(
                (
                    *tuple(anchor.item for anchor in request.enjoyment_anchors),
                    *request.enjoyment_items,
                ),
                key=lambda item: item.item_id,
            )
        ],
    }
    user = (
        FACT_TASK
        + "\n\n"
        + ENJOY_TASK
        + "\n\n"
        + ADJUDICATION_TASK
        + "\n\nBLIND ADJUDICATION INPUT:\n"
        + json.dumps(
            provider_input,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return _request_envelope(
        model=model,
        max_tokens=FACT_MAX_OUTPUT_TOKENS,
        system=SHARED_POLICY,
        schema=_provider_schema(ProviderAdjudicationPayload),
        user=user,
    )


def request_envelope_sha256(envelope: str) -> str:
    return text_sha256(envelope)


__all__ = [
    "ADJUDICATION_PROMPT_SHA256",
    "BATCH_FACT_PROMPT_SHA256",
    "CALIBRATION_PROMPT_SHA256",
    "ENJOY_MAX_OUTPUT_TOKENS",
    "ENJOY_PROMPT_SHA256",
    "FACT_MAX_OUTPUT_TOKENS",
    "FACT_PROMPT_SHA256",
    "MAX_REVIEW_INPUT_BYTES",
    "QUALITY_POLICY_SHA256",
    "QUALITY_SCHEMA_TRANSFORMER_ID",
    "SHARED_POLICY",
    "AdjudicationPayload",
    "CalibrationPayload",
    "EnjoyPayload",
    "FactPayload",
    "ProviderAdjudicationPayload",
    "ProviderCalibrationPayload",
    "ProviderEnjoyPayload",
    "ProviderEnjoymentScoredAssessment",
    "ProviderFactPayload",
    "ProviderFactSentenceVerdict",
    "adjudication_request_envelope",
    "batch_fact_request_envelope",
    "calibration_request_envelope",
    "enjoyment_request_envelope",
    "fact_request_envelope",
    "materialize_provider_enjoyment_assessments",
    "request_envelope_sha256",
]
