"""The ingestion record: pydantic model, hashes, slug, shape detection.

Field names come from Docs/ingestion/rebuild-spec.md §2 and are final (no
version marker). This module is built incrementally, one record-model
capability at a time.

Step 1 gave `validate()` shape detection (a legacy-shape record — neither
'claims' nor 'narration' present — is refused with SHAPE_LEGACY, never
parsed further; a record with exactly one of the two keys, or a non-dict
record, is SHAPE_UNKNOWN) and pydantic schema/vocabulary checks on a
new-shape record (extra='forbid' on every model; `as_of` an int year
1000-2100 or an ISO date string; `rights_basis`, claim `kind` and claim
`status` each drawn from their fixed vocabulary).

Step 3 adds: identity (BEAT_ID_MISMATCH, BEAT_ID_COLLISION,
CLAIM_ID_COLLISION), CLAIM_NO_SOURCE, CC_BY_SA_FIELDS_MISSING, the
contested-values rule (CONTESTED_NEEDS_DIFFERING_VALUES), the arc rule
(ARC_TOO_FEW_CLAIMS, exempting STRUCTURAL_BEAT_TYPES), and the remaining
beat-level vocabularies (`beat_type`, `lenses`, `kid_friendly`,
`resolution.by`).

Step 4 adds span grounding: `validate()` takes an optional `chunks_root`
keyword. When omitted (None), grounding is skipped entirely — every prior
call site keeps working unchanged. When given, every source in every
schema-valid claim is checked against `chunks_root/{source_id}/{chunk}.txt`:
a missing chunk file is SPAN_CHUNK_MISSING (never a silent pass), and a
span that isn't a whitespace-normalized substring of the chunk text is
SPAN_NOT_VERBATIM (decisions.verbatim_means — only whitespace is
normalized, never case or punctuation).

Step 5 adds hash and verdict binding: NARRATION_HASH_STALE when a beat's
narration.claims_hash no longer matches a fresh `claims_hash()` over that
beat's own claims, and VERDICT_UNBOUND when a claim's or the narration's
verdict.bound_to no longer matches a fresh `bind()` over its current judged
text/spans (decisions.pinned_definitions). A claim whose span already
failed grounding (SPAN_CHUNK_MISSING/SPAN_NOT_VERBATIM) is not also checked
for VERDICT_UNBOUND — its bound_to is computed over the very span content
grounding just reported as unverified, so a second error over the same
unverified data would be noise on top of the actionable one.

Step 6 adds judge independence: JUDGE_IS_AUTHOR when a claim's or the
narration's own verdict.judge_model equals that beat's narration.author_model
(Docs/ingestion/rebuild-spec.md §2 — "every claim has a verdict whose
judge_model differs from narration.author_model" — extended to the
narration verdict too by decisions.spec_extensions).

Step 7 adds NARRATION_LIFT: `beat.narration.text` shares an 8+
consecutive-word run with one of the beat's own claim spans
(Docs/ingestion/rebuild-spec.md — "Lift = 8+ consecutive words shared with
a span").

Step 7.5 swaps the run measure for `scripts.verbatim.run_outside_quotation`
— the same longest-shared-run gate the legacy pipeline uses — so a run
sitting entirely inside a quotation that names who said it
(`scripts.verbatim.is_attributed`) is exempt: quoting a named speaker and
citing them is not copying the book that also quoted them. This inherits
that function's word contract as-is (case-folded, punctuation stripped),
including its known gap — `is_attributed` has no guidebook check
(decisions.disclosed_not_fixed) — rather than reimplementing a second,
narrower measure.

Slice 4 adds VERDICT_NOT_ENTAILED: a claim whose verdict.entailed is false
is refused — P3 (src/ingest/judge_claims.py) drops a refused claim, so one
on disk means a gate was bypassed.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from scripts.verbatim import run_outside_quotation
from src.schema.definitions import TAGGABLE_LENSES

CLAIM_KIND_VALUES = ("event", "state", "belief")
CLAIM_STATUS_VALUES = ("resolved", "contested", "superseded")
RIGHTS_BASIS_VALUES = ("owned_copy", "cc_by_sa", "public_domain", "own_work", "permission")
BEAT_TYPE_VALUES = (
    "anecdote",
    "event",
    "character_story",
    "architectural_detail",
    "factoid",
    "sensory_observation",
    "establishing",
    "stop_orientation",
    "transit",
    "sidebar",
)
# The three beat_types the arc rule (ARC_TOO_FEW_CLAIMS) exempts from
# needing >=2 claims — spec's "structural" beats (decisions.pinned_definitions).
STRUCTURAL_BEAT_TYPES = ("stop_orientation", "transit", "sidebar")
# Attribution fields a cc_by_sa source must carry (and every other
# rights_basis must NOT): CC_BY_SA_FIELDS_MISSING (decisions.vocabulary_enforced).
CC_BY_SA_ATTRIBUTION_FIELDS = ("article_title", "url", "revision_id", "section", "retrieved_at")
# A shared run of this many consecutive words is a lift: NARRATION_LIFT
# (Docs/ingestion/rebuild-spec.md — "Lift = 8+ consecutive words shared with
# a span").
NARRATION_LIFT_RUN_LENGTH = 8


def slug(name: str) -> str:
    """NFKD-fold, lowercase, collapse every run of non-[a-z0-9] to one '-',
    strip leading/trailing '-'. Kebab-case.

    Deliberately NOT scripts/beat_builder.slugify
    (decisions.pinned_definitions in run-context.md).
    """
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.lower()
    folded = re.sub(r"[^a-z0-9]+", "-", folded)
    return folded.strip("-")


def bind(text: str, span: str = "") -> str:
    """SHA-256 binding of a judged text to the span(s) that grounded it.

    `bind(text)` (no span) == sha256(text). `bind(text, span)` ==
    sha256(text + NUL + span) — a NUL separator, not '\\n': a '\\n' join
    would let `bind('a\\nb')` collide with `bind('a', '\\nb')`
    (decisions.pinned_definitions).
    """
    payload = text if span == "" else f"{text}\x00{span}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def claims_hash(claims: list[dict[str, Any]]) -> str:
    """SHA-256 of the resolved claims' texts, '\\n'-joined, in file order.

    A beat with no resolved claims hashes the empty string.
    """
    texts = [claim["text"] for claim in claims if claim.get("status") == "resolved"]
    return hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()


def _normalize_ws(text: str) -> str:
    """Whitespace-normalized: collapse and strip runs of whitespace,
    nothing else (decisions.verbatim_means — case and punctuation, en dash
    included, must match exactly)."""
    return " ".join(text.split())


def _validate_as_of(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError("as_of must be an int year or an ISO date string")
    if isinstance(value, int):
        if not (1000 <= value <= 2100):
            raise ValueError("as_of year must be between 1000 and 2100")
        return value
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("as_of string must be an ISO date (YYYY-MM-DD)") from exc
    return value


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    chunk: str
    span: str
    as_of: int | str
    rights_basis: Literal["owned_copy", "cc_by_sa", "public_domain", "own_work", "permission"]
    stated_value: str | int | float | None = None
    # cc_by_sa attribution fields (Rights and licensing in the spec); their
    # presence/absence rule (CC_BY_SA_FIELDS_MISSING) is a later step's check.
    article_title: str | None = None
    url: str | None = None
    revision_id: str | None = None
    section: str | None = None
    retrieved_at: str | None = None

    @field_validator("as_of")
    @classmethod
    def _check_as_of(cls, v: Any) -> Any:
        return _validate_as_of(v)


class Verdict(BaseModel):
    """A claim's judge verdict."""

    model_config = ConfigDict(extra="forbid")

    judge_model: str
    entailed: bool
    bound_to: str


class Resolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    by: Literal["corroborated", "established", "decided"] | None = None
    decided_by: str | None = None
    decided_at: str | None = None


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str
    text: str
    kind: Literal["event", "state", "belief"]
    status: Literal["resolved", "contested", "superseded"]
    sources: list[Source] = Field(min_length=1)
    resolved_value: str | int | float | None = None
    resolution: Resolution | None = None
    verdict: Verdict


class NarrationVerdict(BaseModel):
    """The narration's judge verdict (per-sentence entailment)."""

    model_config = ConfigDict(extra="forbid")

    judge_model: str
    sentences_entailed: int
    sentences_total: int
    bound_to: str


class Narration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    claims_hash: str
    author_model: str
    verdict: NarrationVerdict
    flags: list[str] = []


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")

    held: bool = False
    reason: str | None = None


class Beat(BaseModel):
    """The new-shape ingestion record (Docs/ingestion/rebuild-spec.md §2)."""

    model_config = ConfigDict(extra="forbid")

    beat_id: str
    city_name: str
    poi_name: str
    story_slug: str
    title: str
    beat_type: Literal[
        "anecdote",
        "event",
        "character_story",
        "architectural_detail",
        "factoid",
        "sensory_observation",
        "establishing",
        "stop_orientation",
        "transit",
        "sidebar",
    ]
    lenses: list[str]
    sub_location: str | None = None
    trigger_address: str | None = None
    claims: list[Claim]
    narration: Narration
    physical_cues: list[str] = []
    entities: list[str] = []
    narrative_function: str | None = None
    emotional_register: str | None = None
    sensory_anchor: bool = False
    inline_foreign_phrases: list[str] = []
    pronunciation: str | None = None
    kid_friendly: Literal["yes", "no"] | None = None
    duration_sec: int = 0
    review: Review | None = None

    @field_validator("lenses")
    @classmethod
    def _check_lenses(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("lenses must include at least one value")
        if len(set(v)) != len(v):
            raise ValueError("lenses must not contain duplicates")
        invalid = [lens for lens in v if lens not in TAGGABLE_LENSES]
        if invalid:
            raise ValueError(f"lenses outside TAGGABLE_LENSES: {invalid}")
        return v


def _record_shape(record: Any) -> str:
    """Structural shape per decisions.shape_detection: both 'claims' and
    'narration' present -> 'new'; neither -> 'legacy'; exactly one, or a
    non-dict record -> 'unknown'. Never keys on script_body.
    """
    if not isinstance(record, dict):
        return "unknown"
    has_claims = "claims" in record
    has_narration = "narration" in record
    if has_claims and has_narration:
        return "new"
    if not has_claims and not has_narration:
        return "legacy"
    return "unknown"


# Pydantic error loc segments this step maps to a specific code. Checked
# against every segment of an error's `loc` (not just the last one) because
# a Union-typed field's error `loc` can end in a branch tag (e.g. 'int' /
# 'str' for `as_of: int | str`) rather than the field name itself.
_FIELD_CODES = {
    "as_of": "AS_OF_INVALID",
    "rights_basis": "RIGHTS_BASIS_INVALID",
    "kind": "CLAIM_KIND_INVALID",
    "status": "CLAIM_STATUS_INVALID",
    "sources": "CLAIM_NO_SOURCE",
    "beat_type": "BEAT_TYPE_INVALID",
    "lenses": "LENS_INVALID",
    "kid_friendly": "KID_FRIENDLY_INVALID",
    "by": "RESOLUTION_BY_INVALID",
}


def _error_code_for(err: dict[str, Any]) -> str:
    # Only the last two `loc` segments can name the actually-invalid field:
    # a plain field error's loc ends in (field_name,); a Union-typed field's
    # loc can end in (field_name, branch_tag) instead (the branch pydantic
    # tried, e.g. 'int'/'str' for `as_of: int | str`). Scanning every
    # segment (not just these two) would false-match a container field
    # name — e.g. an extra key nested under `sources` (loc ends in
    # (..., 'sources', 0, 'page')) must stay SCHEMA_INVALID, not
    # CLAIM_NO_SOURCE, even though 'sources' appears earlier in the path.
    loc = err.get("loc", ())
    for loc_part in loc[-2:][::-1]:
        if isinstance(loc_part, str) and loc_part in _FIELD_CODES:
            return _FIELD_CODES[loc_part]
    return "SCHEMA_INVALID"


def _record_label(record: Any, index: int) -> str:
    """'CODE beat_id: detail' when beat_id is present, else 'CODE #index: detail'
    (decisions.pinned_definitions)."""
    if isinstance(record, dict):
        beat_id = record.get("beat_id")
        if isinstance(beat_id, str) and beat_id:
            return beat_id
    return f"#{index}"


def _cc_by_sa_errors(label: str, claim_id: str, source: Source) -> list[str]:
    """CC_BY_SA_FIELDS_MISSING: a cc_by_sa source must carry all five
    attribution fields with a '?oldid=' url; every other rights_basis must
    carry none of them (decisions.vocabulary_enforced)."""
    present = [f for f in CC_BY_SA_ATTRIBUTION_FIELDS if getattr(source, f) is not None]

    if source.rights_basis == "cc_by_sa":
        missing = [f for f in CC_BY_SA_ATTRIBUTION_FIELDS if f not in present]
        bad_url = source.url is not None and "?oldid=" not in source.url
        if not missing and not bad_url:
            return []
        detail_parts = []
        if missing:
            detail_parts.append(f"missing {', '.join(missing)}")
        if bad_url:
            detail_parts.append("url missing '?oldid='")
        return [
            f"CC_BY_SA_FIELDS_MISSING {label}: claim {claim_id} source "
            f"{source.source_id}: {'; '.join(detail_parts)}"
        ]

    if present:
        return [
            f"CC_BY_SA_FIELDS_MISSING {label}: claim {claim_id} source "
            f"{source.source_id}: cc_by_sa attribution fields set on a "
            f"{source.rights_basis!r} source ({', '.join(present)})"
        ]
    return []


def _identity_and_arc_errors(
    label: str, beat: Beat, seen_beat_ids: dict[str, str]
) -> list[str]:
    """Post-schema checks on a schema-valid Beat: identity (BEAT_ID_MISMATCH,
    BEAT_ID_COLLISION, CLAIM_ID_COLLISION), the arc rule (ARC_TOO_FEW_CLAIMS,
    exempting STRUCTURAL_BEAT_TYPES), CONTESTED_NEEDS_DIFFERING_VALUES and
    CC_BY_SA_FIELDS_MISSING. `seen_beat_ids` accumulates across the whole
    `validate()` call so collisions are caught across records.
    """
    errors: list[str] = []

    expected_beat_id = f"{beat.city_name}/{slug(beat.poi_name)}/{beat.story_slug}"
    if beat.beat_id != expected_beat_id:
        errors.append(
            f"BEAT_ID_MISMATCH {label}: beat_id does not equal "
            f"city_name/slug(poi_name)/story_slug (expected {expected_beat_id!r})"
        )

    if beat.beat_id in seen_beat_ids:
        errors.append(
            f"BEAT_ID_COLLISION {label}: beat_id already used by "
            f"{seen_beat_ids[beat.beat_id]}"
        )
    else:
        seen_beat_ids[beat.beat_id] = label

    if beat.beat_type not in STRUCTURAL_BEAT_TYPES and len(beat.claims) < 2:
        errors.append(
            f"ARC_TOO_FEW_CLAIMS {label}: beat_type {beat.beat_type!r} "
            f"requires >=2 claims, has {len(beat.claims)}"
        )

    seen_claim_ids: set[str] = set()
    for claim in beat.claims:
        if claim.claim_id in seen_claim_ids:
            errors.append(
                f"CLAIM_ID_COLLISION {label}: claim_id {claim.claim_id!r} used more than once"
            )
        else:
            seen_claim_ids.add(claim.claim_id)

        if claim.status == "contested":
            distinct_values = {
                s.stated_value for s in claim.sources if s.stated_value is not None
            }
            if len(claim.sources) < 2 or len(distinct_values) < 2:
                errors.append(
                    f"CONTESTED_NEEDS_DIFFERING_VALUES {label}: claim {claim.claim_id} "
                    "is contested but its sources do not carry two differing stated_value"
                )

        for source in claim.sources:
            errors.extend(_cc_by_sa_errors(label, claim.claim_id, source))

    return errors


def _judge_independence_errors(label: str, beat: Beat) -> list[str]:
    """JUDGE_IS_AUTHOR: a claim's or the narration's own verdict.judge_model
    must never equal that beat's narration.author_model — the judge may
    never include the author (decisions.spec_extensions extends the base
    per-claim rule to the narration verdict too).
    """
    errors: list[str] = []
    author_model = beat.narration.author_model

    for claim in beat.claims:
        if claim.verdict.judge_model == author_model:
            errors.append(
                f"JUDGE_IS_AUTHOR {label}: claim {claim.claim_id} verdict.judge_model "
                f"equals narration.author_model ({author_model!r})"
            )

    if beat.narration.verdict.judge_model == author_model:
        errors.append(
            f"JUDGE_IS_AUTHOR {label}: narration verdict.judge_model equals "
            f"narration.author_model ({author_model!r})"
        )

    return errors


def _verdict_entailed_errors(label: str, beat: Beat) -> list[str]:
    """VERDICT_NOT_ENTAILED: a claim whose own judge refused it
    (`verdict.entailed` false) must never reach disk — P3 drops such a
    claim after its one re-ask (src/ingest/judge_claims.py), so one on
    disk is a bypassed gate, not a record of a decision."""
    return [
        f"VERDICT_NOT_ENTAILED {label}: claim {claim.claim_id} verdict.entailed is false"
        for claim in beat.claims
        if not claim.verdict.entailed
    ]


def _narration_lift_errors(label: str, beat: Beat) -> list[str]:
    """NARRATION_LIFT: `beat.narration.text` shares an 8+ word run, measured
    outside attributed quotation, with one of the beat's own claim spans
    (`scripts.verbatim.run_outside_quotation` — see the module docstring).
    Reports the first source span (claim/source order) whose run clears the
    block; stops there rather than listing every span a narration lifts from.
    """
    for claim in beat.claims:
        for source in claim.sources:
            found = run_outside_quotation(beat.narration.text, source.span)
            if found["length"] >= NARRATION_LIFT_RUN_LENGTH:
                return [
                    f"NARRATION_LIFT {label}: narration.text shares an "
                    f"{found['length']}-word run with a source span: {found['text']!r}"
                ]
    return []


def _span_errors(chunks_root: Path, label: str, beat: Beat) -> tuple[list[str], set[str]]:
    """Span grounding for every source of every claim in a schema-valid
    beat, against `chunks_root/{source_id}/{chunk}.txt` (decisions.
    chunks_root — uniform lookup, no per-book directory mapping). A missing
    chunk file is SPAN_CHUNK_MISSING, never a silent pass; a span that
    isn't a whitespace-normalized substring of the chunk text is
    SPAN_NOT_VERBATIM. Both name the beat, claim_id and source_id (AC-13).

    Returns `(errors, flagged_claim_ids)` — the claim_ids of every claim
    with at least one grounding failure, so `_hash_and_verdict_errors` can
    skip VERDICT_UNBOUND for a claim whose bound_to is computed over span
    content already reported as unverified.
    """
    errors: list[str] = []
    flagged_claim_ids: set[str] = set()
    for claim in beat.claims:
        for source in claim.sources:
            chunk_path = chunks_root / source.source_id / f"{source.chunk}.txt"
            if not chunk_path.exists():
                errors.append(
                    f"SPAN_CHUNK_MISSING {label}: claim {claim.claim_id} source "
                    f"{source.source_id}: chunk file not found at {chunk_path}"
                )
                flagged_claim_ids.add(claim.claim_id)
                continue
            chunk_text = chunk_path.read_text(encoding="utf-8")
            if _normalize_ws(source.span) not in _normalize_ws(chunk_text):
                errors.append(
                    f"SPAN_NOT_VERBATIM {label}: claim {claim.claim_id} source "
                    f"{source.source_id}: span not verbatim in {source.chunk}"
                )
                flagged_claim_ids.add(claim.claim_id)
    return errors, flagged_claim_ids


def _hash_and_verdict_errors(
    label: str, beat: Beat, skip_claim_ids: set[str] | None = None
) -> list[str]:
    """NARRATION_HASH_STALE and VERDICT_UNBOUND (decisions.pinned_definitions).

    `skip_claim_ids` (claim_ids already flagged by `_span_errors`) excludes
    a claim's VERDICT_UNBOUND check — see the module docstring.
    """
    skip_claim_ids = skip_claim_ids or set()
    errors: list[str] = []

    expected_claims_hash = claims_hash(
        [{"text": claim.text, "status": claim.status} for claim in beat.claims]
    )
    if beat.narration.claims_hash != expected_claims_hash:
        errors.append(
            f"NARRATION_HASH_STALE {label}: narration.claims_hash does not match "
            "a fresh recomputation over this beat's claims"
        )

    for claim in beat.claims:
        if claim.claim_id in skip_claim_ids:
            continue
        expected_bound_to = bind(claim.text, "\n".join(source.span for source in claim.sources))
        if claim.verdict.bound_to != expected_bound_to:
            errors.append(
                f"VERDICT_UNBOUND {label}: claim {claim.claim_id} verdict.bound_to "
                "does not match bind(text, sources)"
            )

    expected_narration_bound_to = bind(beat.narration.text)
    if beat.narration.verdict.bound_to != expected_narration_bound_to:
        errors.append(
            f"VERDICT_UNBOUND {label}: narration verdict.bound_to does not match "
            "bind(narration.text)"
        )

    return errors


def validate(records: list[dict[str, Any]], chunks_root: str | Path | None = None) -> list[str]:
    """Validate a list of raw beat records (as read from beats.json).

    Returns a list of error strings, each 'CODE label: detail'. Never
    raises — a legacy or malformed record is reported, not thrown.

    This step enforces shape (new vs legacy vs unknown), pydantic
    schema/vocabulary rules on a new-shape record (extra='forbid', as_of,
    rights_basis, claim kind/status), the identity/arc/CC-BY-SA/contested
    rules, hash and verdict binding (NARRATION_HASH_STALE, VERDICT_UNBOUND),
    judge independence (JUDGE_IS_AUTHOR), narration lift (NARRATION_LIFT),
    and — when `chunks_root` is given — span grounding (see the module
    docstring). `chunks_root=None` (the default) skips span grounding
    entirely, so every pre-step-4 call site is unaffected. See the module
    docstring for what is deliberately not checked yet.
    """
    root = Path(chunks_root) if chunks_root is not None else None
    errors: list[str] = []
    seen_beat_ids: dict[str, str] = {}
    for index, record in enumerate(records):
        label = _record_label(record, index)
        shape = _record_shape(record)
        if shape == "legacy":
            errors.append(f"SHAPE_LEGACY {label}: legacy-shape record, refused")
            continue
        if shape == "unknown":
            errors.append(f"SHAPE_UNKNOWN {label}: record shape is neither new nor legacy")
            continue
        try:
            beat = Beat.model_validate(record)
        except ValidationError as exc:
            for err in exc.errors():
                code = _error_code_for(err)
                detail = err.get("msg", "invalid")
                errors.append(f"{code} {label}: {detail}")
            continue
        errors.extend(_identity_and_arc_errors(label, beat, seen_beat_ids))
        errors.extend(_judge_independence_errors(label, beat))
        errors.extend(_verdict_entailed_errors(label, beat))
        errors.extend(_narration_lift_errors(label, beat))
        span_errs: list[str] = []
        span_flagged_claim_ids: set[str] = set()
        if root is not None:
            span_errs, span_flagged_claim_ids = _span_errors(root, label, beat)
        errors.extend(_hash_and_verdict_errors(label, beat, skip_claim_ids=span_flagged_claim_ids))
        errors.extend(span_errs)
    return errors
