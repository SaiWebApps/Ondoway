"""The ingest LLM seam: roles, phases, prices, and the ModelClient contract.

Built incrementally, one atomic step at a time, for the rebuilt content
pipeline (Docs/ingestion/rebuild-spec.md).

Step 1 lands the vocabulary every later step builds on: the role/phase
tables (`ROLE_MODEL`, `PHASE_ROLE`, `BATCH_PHASES`, `JUDGE_ROLES`), the
pinned price table (`PRICES_USD_PER_MTOK`, `PRICES_CACHED_ON`,
`BATCH_DISCOUNT`), `same_model()` (dated-snapshot-suffix normalization for
the judge-independence check), the frozen result dataclasses
(`Completion`, `Usage`, `BatchFailure`, `PhaseCall`, `PhaseCost`,
`CostEstimate`, `MockAnswer`, `MockFailure`), the `EventSink` type alias,
the `LlmError` hierarchy, and the `ModelClient` Protocol.

Step 2 adds `MockClient`'s construction (layer (a) of the judge-
independence gate: refuses `JudgeIsAuthor` at `__init__` when a judge
role's configured model id names the author's, before any call or
event), `count_tokens()`, and `estimate()` — arithmetic over a plan of
`PhaseCall` rows, `count_tokens` memoized once per (model id, unit)
regardless of how many plan rows share that model, `UnknownModelPrice`
refused before any counting, and the `cost_estimate` event emitted to
the sink.

Step 3 adds `MockClient.complete()`: the full sync-path validation order
(`UnknownRole`/`UnknownPhase`/`WrongTransport`/`PhaseRoleMismatch`, all
before the per-instance estimate gate), the estimate gate itself
(`EstimateNotPrinted`), drawing the next scripted answer
(`MockScriptExhausted` naming the role when the script is empty — the
mock never fabricates), and the post-response refusals
(`EmptyCompletion`, `TruncatedCompletion` with the partial text attached,
layer (b) of the judge-independence gate via `same_model`). `estimate()`
also gains its own `UnknownRole` check for a plan row naming a role
absent from `roles`. `AnthropicClient` (the full `ModelClient` contract)
is a later step.

Step 4 adds `MockClient.complete_batch()`: the same validation order as
`complete()` (`UnknownRole`/`UnknownPhase`/`WrongTransport`/
`PhaseRoleMismatch`), plus the Batch API's own custom_id contract —
`^[a-zA-Z0-9_-]{1,64}$` and no duplicates within one call, both a raw
`ValueError` — checked before the per-instance estimate gate; drawing
each unit's scripted `batch_answers[custom_id]` entry
(`MockScriptExhausted` naming the custom_id when none is scripted); a
scripted `MockFailure` surfaces as a `BatchFailure` in the result dict
rather than being dropped; the same post-response refusals as
`complete()` apply to every `MockAnswer` entry.

Step 6 adds `AnthropicClient`'s construction and `count_tokens()`: the
same layer (a) judge-as-author refusal as `MockClient`, the pinned
constructor signature (`sdk`/`submit_sdk` injectable, `roles`,
`poll_interval_s`, `max_poll_s`), and the two BOUNDED SDK objects it
falls back to when nothing is injected — the sync object from
`src.tour.anthropic_client.batch_review_client()` (300 s / 3 retries:
offline review, nothing waiting) and the batch-SUBMISSION object from
`src.tour.batch_transport.batch_client()` (zero retries — a hidden SDK
retry could double-submit a batch, i.e. double-spend). Both are built
LAZILY on first use, through a module-attribute lookup inside the
method, so importing this module never imports the SDK (AC-21) and a
test can monkeypatch either factory. `complete()` lands in step 7 and
`complete_batch()` in step 8.

Step 7 adds `AnthropicClient.estimate()` (the same arithmetic as
`MockClient.estimate()`, factored into the shared `_price_plan()` helper
so both clients price a plan identically — priced against this client's
own `count_tokens()`, i.e. the real provider endpoint) and
`AnthropicClient.complete()`: the same call-order validation and
estimate gate as `MockClient.complete()`, then a request built to the
EXACT pinned shape (decisions.sdk_shapes) — `{model, max_tokens,
messages: [{role: 'user', content: prompt}]}` plus `output_config`
`{'format': {'type': 'json_schema', 'schema': schema}}` iff `schema` is
not `None`, and nothing else (no `thinking`, no `system`, no prefill, no
`output_format` — SDK 0.97.0 verified). The response's text comes from
concatenating its `type == 'text'` content blocks (empty or
whitespace-only -> `EmptyCompletion`, mirroring `MockClient` but never
raising the batch path's `ValueError` for missing content — that
distinction is `decisions.call_order`'s disclosed EXCEPTION, not
reproduced here); `model_id` comes from `response.model` (the response
string, never `ROLE_MODEL`); `stop_reason == 'max_tokens'` ->
`TruncatedCompletion` with the partial text attached; a judge role
answering with the author's model id -> `JudgeIsAuthor` (layer (b), via
`same_model`, same as `MockClient`). `complete_batch()` lands in step 8.

Step 8 adds `AnthropicClient.complete_batch()`: the same call-order
validation, custom_id contract and estimate gate as
`MockClient.complete_batch()`, then the whole Batch API round through
`src.tour.batch_transport` — `submit_batch()` with the ZERO-RETRY
submission client and one pinned request body per unit (the same
`_message_kwargs()` the sync path sends, so the two transports can never
drift), the `batch_submitted` event carrying the returned batch id,
`poll_batch()` (bounded by this client's `poll_interval_s`/`max_poll_s`)
and `collect_results()`. The result dict is built by iterating the
SUBMITTED prompts, never the results stream, so a unit the provider never
mentions becomes a `BatchFailure('missing')` instead of being silently
dropped; errored/canceled/expired units keep their provider error text.
NOTE (decisions.call_order's disclosed EXCEPTION): on this path an
empty-text SUCCEEDED unit raises `ValueError` from inside
`collect_results` (via `certification_provider._response_text`) for the
whole collection before `EmptyCompletion` can apply — that is src/tour
behaviour, disclosed and not fixed by this slice.

The sonnet-5 price row (2.0, 10.0) is pinned from the claude-api skill
(cached 2026-06-24) even though src/tour/provider_text_review.py:39
(2026-08-28) carries (3.0, 15.0) for a "sonnet-5" key — that file's row is
presumed a stale copy of sonnet-4-6's price. This slice does not touch
provider_text_review.py; the two tables intentionally disagree until a
human rules on it (decisions.spec_extensions #11 in run-context.md).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Literal, Protocol

# ── Role / phase tables (decisions.vocabulary_pinned) ──────────────────────

#: Every role's model id. Read-only (MappingProxyType raises TypeError on
#: item assignment) so no call site can mutate the shared table in place.
ROLE_MODEL: MappingProxyType[str, str] = MappingProxyType(
    {
        "author": "claude-opus-5",
        "claim_judge": "claude-haiku-4-5",
        "narration_judge": "claude-haiku-4-5",
        "merge_judge": "claude-sonnet-5",
    }
)

#: Every pipeline phase's owning role. Also read-only.
PHASE_ROLE: MappingProxyType[str, str] = MappingProxyType(
    {
        "P1": "author",
        "P2": "author",
        "P3": "claim_judge",
        "P4": "author",
        "P5": "narration_judge",
        "P6": "merge_judge",
    }
)

#: Phases that must go through the Batch API rather than the sync path.
BATCH_PHASES: frozenset[str] = frozenset({"P1", "P3", "P5"})

#: Roles that judge another role's output — never the author's own model id
#: (JudgeIsAuthor, both at construction and per-response; see later steps).
JUDGE_ROLES: frozenset[str] = frozenset({"claim_judge", "narration_judge", "merge_judge"})

# ── Price table (decisions.vocabulary_pinned) ──────────────────────────────

#: (input_usd_per_mtok, output_usd_per_mtok) per model id. Covers every
#: ROLE_MODEL value — nothing may be estimated against an unpriced model
#: (see UnknownModelPrice).
PRICES_USD_PER_MTOK: MappingProxyType[str, tuple[float, float]] = MappingProxyType(
    {
        "claude-opus-5": (5.0, 25.0),
        "claude-sonnet-5": (2.0, 10.0),
        "claude-haiku-4-5": (1.0, 5.0),
    }
)

#: The date PRICES_USD_PER_MTOK was last verified against the provider.
PRICES_CACHED_ON = "2026-06-24"

#: Batch API pricing multiplier applied to every batch-phase row.
BATCH_DISCOUNT = 0.5


def same_model(a: str, b: str) -> bool:
    """True iff `a` and `b` name the same model once at most one trailing
    ``-YYYYMMDD`` dated-snapshot suffix is stripped from each side
    independently.

    ``same_model('claude-opus-5-20260115', 'claude-opus-5')`` is True (the
    dated snapshot IS that model); ``same_model('claude-opus-5-1',
    'claude-opus-5')`` is False (``-1`` is not a date, so nothing is
    stripped and the strings differ); at most one suffix is stripped per
    side, so a second trailing date is never eaten too
    (decisions.spec_extensions #3).
    """
    return _strip_one_dated_suffix(a) == _strip_one_dated_suffix(b)


_DATED_SUFFIX_RE = re.compile(r"-\d{8}$")


def _strip_one_dated_suffix(model_id: str) -> str:
    return _DATED_SUFFIX_RE.sub("", model_id, count=1)


# ── EventSink (decisions.vocabulary_pinned) ─────────────────────────────────

#: A sink callable: (event_kind, payload) -> None. Steps 2+ call it with
#: 'cost_estimate' (CostEstimate.as_dict()) and 'batch_submitted'
#: ({phase, role, batch_id, count}).
EventSink = Callable[[str, dict], None]


# ── Result dataclasses (decisions.vocabulary_pinned) ────────────────────────


@dataclass(frozen=True)
class Usage:
    """Token/billing accounting for one Completion."""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    batch: bool
    batch_id: str | None
    request_id: str | None


@dataclass(frozen=True)
class Completion:
    """One successful model response."""

    text: str
    model_id: str
    usage: Usage
    stop_reason: str


@dataclass(frozen=True)
class BatchFailure:
    """One batch unit that did not come back as a usable Completion."""

    custom_id: str
    result_type: Literal["errored", "canceled", "expired", "missing"]
    error_message: str | None
    batch_id: str


@dataclass(frozen=True)
class PhaseCall:
    """One row of a cost-estimate plan: how many calls a phase/role makes
    per unit, and the token shape of each call."""

    phase: str
    role: str
    calls_per_unit: int
    overhead_tokens: int
    expected_output_tokens: int


@dataclass(frozen=True)
class PhaseCost:
    """One priced row of a CostEstimate, in plan order."""

    phase: str
    role: str
    model_id: str
    batch: bool
    calls: int
    input_tokens: int
    output_tokens: int
    usd: float


@dataclass(frozen=True)
class CostEstimate:
    """The full estimate a ModelClient must print (emit to its EventSink)
    before any completion is allowed (see EstimateNotPrinted)."""

    units: int
    rows: list[PhaseCost]
    total_input_tokens: int
    total_output_tokens: int
    total_usd: float
    prices_cached_on: str

    def as_dict(self) -> dict:
        """JSON-safe payload — round-trips through json.dumps/json.loads."""
        return {
            "units": self.units,
            "rows": [asdict(row) for row in self.rows],
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_usd": self.total_usd,
            "prices_cached_on": self.prices_cached_on,
        }


@dataclass(frozen=True)
class MockAnswer:
    """A scripted MockClient sync (or per-unit batch) response."""

    text: str
    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = "end_turn"


@dataclass(frozen=True)
class MockFailure:
    """A scripted MockClient batch-unit failure."""

    result_type: str
    error_message: str | None = None


# ── Errors (decisions.vocabulary_pinned) ────────────────────────────────────


class LlmError(ValueError):
    """Base of every ingest LLM error."""


class JudgeIsAuthor(LlmError):  # noqa: N818 — pinned name is the spec/test contract
    """A judge role's model id equals the author's — by configuration
    (construction) or by the response naming the author's model id."""

    code = "JUDGE_IS_AUTHOR"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "JUDGE_IS_AUTHOR"


class EstimateNotPrinted(LlmError):  # noqa: N818 — pinned name is the spec/test contract
    """A completion was requested before the cost estimate was emitted on
    this client instance."""


class WrongTransport(LlmError):  # noqa: N818 — pinned name is the spec/test contract
    """A batch-phase call went through the sync method, or a sync-phase
    call went through the batch method."""


class PhaseRoleMismatch(LlmError):  # noqa: N818 — pinned name is the spec/test contract
    """The role passed does not own the given phase (PHASE_ROLE)."""


class UnknownRole(LlmError):  # noqa: N818 — pinned name is the spec/test contract
    """The role passed is not a key of ROLE_MODEL."""


class UnknownPhase(LlmError):  # noqa: N818 — pinned name is the spec/test contract
    """The phase passed is not a key of PHASE_ROLE."""


class UnknownModelPrice(LlmError):  # noqa: N818 — pinned name is the spec/test contract
    """A model named by the plan has no row in the prices table passed to
    estimate()."""


class EmptyCompletion(LlmError):  # noqa: N818 — pinned name is the spec/test contract
    """The response text was empty or whitespace-only."""


class TruncatedCompletion(LlmError):  # noqa: N818 — pinned name is the spec/test contract
    """The response stopped for reaching max_tokens; `.text` carries the
    partial text the provider still returned."""

    def __init__(self, message: str, text: str) -> None:
        super().__init__(message)
        self.text = text


class MockScriptExhausted(LlmError):  # noqa: N818 — pinned name is the spec/test contract
    """A MockClient answer/batch_answers script had no entry left for the
    role (sync) or custom_id (batch) being called — the mock never
    fabricates an answer."""


# ── ModelClient contract (decisions.vocabulary_pinned) ──────────────────────


class ModelClient(Protocol):
    """The seam later steps' MockClient and AnthropicClient both implement.

    Call order (both `complete` and `complete_batch`, both implementations,
    per decisions.call_order): (1) validate role/phase/transport ->
    UnknownRole/UnknownPhase/WrongTransport/PhaseRoleMismatch, zero calls;
    (2) the per-instance estimate gate -> EstimateNotPrinted, zero calls;
    (3) the call; (4) post-response checks -> EmptyCompletion,
    TruncatedCompletion, JudgeIsAuthor (same_model against the author's
    configured model id).
    """

    def count_tokens(self, model_id: str, text: str) -> int:
        """Provider token count for `text` under `model_id`."""
        ...

    def estimate(
        self,
        units: Sequence[str],
        plan: Sequence[PhaseCall],
        *,
        prices: MappingProxyType[str, tuple[float, float]] = PRICES_USD_PER_MTOK,
    ) -> CostEstimate:
        """Price `plan` over `units`, emit it to the EventSink, and arm this
        instance's estimate gate. Refuses UnknownModelPrice before any
        count_tokens call when `plan` names a model absent from `prices`."""
        ...

    def complete(
        self,
        role: str,
        prompt: str,
        schema: dict | None,
        *,
        phase: str,
        max_tokens: int,
    ) -> Completion:
        """One sync-path completion for a non-batch phase."""
        ...

    def complete_batch(
        self,
        role: str,
        prompts: Sequence[tuple[str, str]],
        schema: dict | None,
        *,
        phase: str,
        max_tokens: int,
    ) -> dict[str, Completion | BatchFailure]:
        """One Batch API round for a batch phase. `prompts` is a sequence of
        (custom_id, prompt) pairs; the result is keyed by custom_id, one
        entry per submitted prompt (an omitted unit surfaces as a 'missing'
        BatchFailure rather than being silently dropped)."""
        ...


# ── MockClient (decisions.vocabulary_pinned) ────────────────────────────────


#: The Batch API's own custom_id contract (mirrors
#: src/tour/batch_transport.py's `_CUSTOM_ID_PATTERN`). Enforced here too so
#: a bad id is a $0 local ValueError on the mock, never only discovered
#: against the real transport (decisions.vocabulary_pinned / AC-17).
_CUSTOM_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _refuse_if_judge_is_author(roles: dict[str, str]) -> None:
    """Layer (a) of the judge-independence gate (decisions.call_order):
    raise JudgeIsAuthor if any judge role's configured model id names the
    same model as the author's (same_model, so a dated snapshot cannot
    dodge it) — checked at construction, before any call or event. Shared
    by MockClient and (step 6+) AnthropicClient."""
    author_model = roles.get("author")
    if author_model is None:
        return
    for judge_role in JUDGE_ROLES:
        judge_model = roles.get(judge_role)
        if judge_model is not None and same_model(judge_model, author_model):
            raise JudgeIsAuthor(
                f"role {judge_role!r} is configured with the author's model id "
                f"({judge_model!r} matches {author_model!r})"
            )


def _price_plan(
    roles: dict[str, str],
    count_tokens: Callable[[str, str], int],
    units: Sequence[str],
    plan: Sequence[PhaseCall],
    prices: MappingProxyType[str, tuple[float, float]],
) -> CostEstimate:
    """The `estimate()` arithmetic shared by MockClient and AnthropicClient
    (step 7): identical pricing regardless of which client's `count_tokens`
    supplies the counts. Refuses UnknownModelPrice before any count_tokens
    call when `plan` names a model absent from `prices`; `count_tokens` is
    called at most once per distinct (model id, unit) pair across the whole
    plan, never once per plan row. An empty `units` yields zero rows and a
    $0 total. Does not touch the sink or an estimate-gate flag — the caller
    emits the 'cost_estimate' event and arms its own instance's gate."""
    for call in plan:
        if call.role not in roles:
            raise UnknownRole(f"unknown role {call.role!r}")
        model_id = roles[call.role]
        if model_id not in prices:
            raise UnknownModelPrice(f"no price for model {model_id!r} (role {call.role!r})")

    rows: list[PhaseCost] = []
    total_input = 0
    total_output = 0
    total_usd = 0.0
    if units:
        token_cache: dict[tuple[str, str], int] = {}
        for call in plan:
            model_id = roles[call.role]
            per_unit_total = 0
            for unit in units:
                key = (model_id, unit)
                if key not in token_cache:
                    token_cache[key] = count_tokens(model_id, unit)
                per_unit_total += token_cache[key] + call.overhead_tokens
            row_calls = call.calls_per_unit * len(units)
            row_input = call.calls_per_unit * per_unit_total
            row_output = row_calls * call.expected_output_tokens
            price_in, price_out = prices[model_id]
            usd = (row_input / 1_000_000) * price_in + (row_output / 1_000_000) * price_out
            batch = call.phase in BATCH_PHASES
            if batch:
                usd *= BATCH_DISCOUNT
            rows.append(
                PhaseCost(
                    phase=call.phase,
                    role=call.role,
                    model_id=model_id,
                    batch=batch,
                    calls=row_calls,
                    input_tokens=row_input,
                    output_tokens=row_output,
                    usd=usd,
                )
            )
            total_input += row_input
            total_output += row_output
            total_usd += usd

    return CostEstimate(
        units=len(units),
        rows=rows,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_usd=total_usd,
        prices_cached_on=PRICES_CACHED_ON,
    )


class MockClient:
    """A scripted ModelClient for tests: never touches the network, never
    fabricates an answer (MockScriptExhausted, steps 3-4), and enforces the
    same call-order gates real clients must (decisions.call_order).

    `answers` scripts sync `complete()` calls per role:
    {role: [MockAnswer, ...]}, consumed in order. `batch_answers` scripts
    `complete_batch()` calls per custom_id: {custom_id: MockAnswer |
    MockFailure}. Step 2 builds construction (judge-as-author refusal at
    layer (a)), `count_tokens()` and `estimate()`; step 3 builds
    `complete()`; `complete_batch()` lands in step 4.
    """

    def __init__(
        self,
        events: EventSink,
        *,
        answers: dict[str, list[MockAnswer]] | None = None,
        batch_answers: dict[str, MockAnswer | MockFailure] | None = None,
        count_tokens_fn: Callable[[str, str], int] | None = None,
        roles: MappingProxyType[str, str] | dict[str, str] = ROLE_MODEL,
    ) -> None:
        roles_dict = dict(roles)
        _refuse_if_judge_is_author(roles_dict)
        self._sink = events
        self.roles = roles_dict
        self._answers = {role: list(scripted) for role, scripted in (answers or {}).items()}
        self._batch_answers = dict(batch_answers or {})
        self._count_tokens_fn = count_tokens_fn or (
            lambda _model_id, text: max(1, len(text) // 4)
        )
        self.calls: list[tuple[str, str]] = []
        self.count_calls: list[tuple[str, str]] = []
        self._estimate_printed = False

    def count_tokens(self, model_id: str, text: str) -> int:
        """Provider token count for `text` under `model_id`; every call is
        recorded in `self.count_calls` (estimate() memoizes so this is only
        called once per distinct (model_id, unit) pair)."""
        self.count_calls.append((model_id, text))
        return self._count_tokens_fn(model_id, text)

    def estimate(
        self,
        units: Sequence[str],
        plan: Sequence[PhaseCall],
        *,
        prices: MappingProxyType[str, tuple[float, float]] = PRICES_USD_PER_MTOK,
    ) -> CostEstimate:
        """Price `plan` over `units` (arithmetic in the shared `_price_plan`
        helper — identical for MockClient and AnthropicClient), emit the
        result to the EventSink as 'cost_estimate', and arm this instance's
        estimate gate. Refuses UnknownModelPrice before any count_tokens
        call when `plan` names a model absent from `prices`. count_tokens is
        called at most once per distinct (model id, unit) pair across the
        whole plan, never once per plan row. An empty `units` yields zero
        rows and a $0 total, but the event is still emitted."""
        result = _price_plan(self.roles, self.count_tokens, units, plan, prices)
        self._sink("cost_estimate", result.as_dict())
        self._estimate_printed = True
        return result

    def complete(
        self,
        role: str,
        prompt: str,
        schema: dict | None,
        *,
        phase: str,
        max_tokens: int,
    ) -> Completion:
        """One sync-path completion, enforcing decisions.call_order: (1)
        role/phase/transport/phase-role validation ->
        UnknownRole/UnknownPhase/WrongTransport/PhaseRoleMismatch, zero
        calls; (2) the per-instance estimate gate -> EstimateNotPrinted,
        zero calls; (3) draw the next scripted `answers[role]` entry
        (MockScriptExhausted naming `role` if none is left — the mock
        never fabricates); (4) post-response checks -> EmptyCompletion
        (empty/whitespace text), TruncatedCompletion (stop_reason ==
        'max_tokens', partial text attached), JudgeIsAuthor (a judge role
        answering with the author's model id, via same_model)."""
        if role not in ROLE_MODEL:
            raise UnknownRole(f"unknown role {role!r}")
        if phase not in PHASE_ROLE:
            raise UnknownPhase(f"unknown phase {phase!r}")
        if phase in BATCH_PHASES:
            raise WrongTransport(f"phase {phase!r} is a batch phase; use complete_batch()")
        if PHASE_ROLE[phase] != role:
            raise PhaseRoleMismatch(
                f"phase {phase!r} is owned by role {PHASE_ROLE[phase]!r}, not {role!r}"
            )
        if not self._estimate_printed:
            raise EstimateNotPrinted(
                "estimate() must be called on this client before complete()"
            )

        scripted = self._answers.get(role) or []
        if not scripted:
            raise MockScriptExhausted(f"no scripted answer left for role {role!r}")
        answer = scripted.pop(0)
        self.calls.append((role, phase))

        if not answer.text.strip():
            raise EmptyCompletion(f"role {role!r} phase {phase!r} returned empty text")
        if answer.stop_reason == "max_tokens":
            raise TruncatedCompletion(
                f"role {role!r} phase {phase!r} truncated at max_tokens", answer.text
            )
        if role in JUDGE_ROLES:
            author_model = self.roles.get("author")
            if author_model is not None and same_model(answer.model_id, author_model):
                raise JudgeIsAuthor(
                    f"role {role!r} answered with the author's model id "
                    f"({answer.model_id!r} matches {author_model!r})"
                )

        return Completion(
            text=answer.text,
            model_id=answer.model_id,
            usage=Usage(
                input_tokens=answer.input_tokens,
                output_tokens=answer.output_tokens,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                batch=False,
                batch_id=None,
                request_id=None,
            ),
            stop_reason=answer.stop_reason,
        )

    def complete_batch(
        self,
        role: str,
        prompts: Sequence[tuple[str, str]],
        schema: dict | None,
        *,
        phase: str,
        max_tokens: int,
    ) -> dict[str, Completion | BatchFailure]:
        """One Batch API round, enforcing decisions.call_order: (1)
        role/phase/transport/phase-role validation ->
        UnknownRole/UnknownPhase/WrongTransport/PhaseRoleMismatch, then the
        Batch API's own custom_id contract (a raw ValueError for a bad
        pattern or a duplicate — the same ^[a-zA-Z0-9_-]{1,64}$ rule
        batch_transport.py enforces, checked here so a bad id is a $0
        local refusal rather than a 400 after real requests are built) —
        zero calls in every case; (2) the per-instance estimate gate ->
        EstimateNotPrinted, zero calls; (3) draw each unit's scripted
        `batch_answers[custom_id]` entry (MockScriptExhausted naming the
        custom_id if none is scripted — the mock never fabricates a batch
        answer, same as complete()); a MockFailure entry is surfaced as a
        BatchFailure in the result, never dropped; (4) post-response
        checks on every MockAnswer entry -> EmptyCompletion,
        TruncatedCompletion, JudgeIsAuthor (same as complete())."""
        if role not in ROLE_MODEL:
            raise UnknownRole(f"unknown role {role!r}")
        if phase not in PHASE_ROLE:
            raise UnknownPhase(f"unknown phase {phase!r}")
        if phase not in BATCH_PHASES:
            raise WrongTransport(f"phase {phase!r} is not a batch phase; use complete()")
        if PHASE_ROLE[phase] != role:
            raise PhaseRoleMismatch(
                f"phase {phase!r} is owned by role {PHASE_ROLE[phase]!r}, not {role!r}"
            )

        seen_ids: set[str] = set()
        for custom_id, _prompt in prompts:
            if not _CUSTOM_ID_PATTERN.fullmatch(custom_id):
                raise ValueError(
                    f"custom_id {custom_id!r} violates the Batch API pattern "
                    "^[a-zA-Z0-9_-]{1,64}$"
                )
            if custom_id in seen_ids:
                raise ValueError(f"duplicate custom_id {custom_id!r}")
            seen_ids.add(custom_id)

        if not self._estimate_printed:
            raise EstimateNotPrinted(
                "estimate() must be called on this client before complete_batch()"
            )

        batch_id = f"mock-batch-{uuid.uuid4()}"
        results: dict[str, Completion | BatchFailure] = {}
        for custom_id, _prompt in prompts:
            scripted = self._batch_answers.get(custom_id)
            if scripted is None:
                raise MockScriptExhausted(
                    f"no scripted batch answer for custom_id {custom_id!r}"
                )
            self.calls.append((role, phase))

            if isinstance(scripted, MockFailure):
                results[custom_id] = BatchFailure(
                    custom_id=custom_id,
                    result_type=scripted.result_type,
                    error_message=scripted.error_message,
                    batch_id=batch_id,
                )
                continue

            answer = scripted
            if not answer.text.strip():
                raise EmptyCompletion(
                    f"role {role!r} phase {phase!r} custom_id {custom_id!r} returned "
                    "empty text"
                )
            if answer.stop_reason == "max_tokens":
                raise TruncatedCompletion(
                    f"role {role!r} phase {phase!r} custom_id {custom_id!r} truncated "
                    "at max_tokens",
                    answer.text,
                )
            if role in JUDGE_ROLES:
                author_model = self.roles.get("author")
                if author_model is not None and same_model(answer.model_id, author_model):
                    raise JudgeIsAuthor(
                        f"role {role!r} custom_id {custom_id!r} answered with the "
                        f"author's model id ({answer.model_id!r} matches {author_model!r})"
                    )

            results[custom_id] = Completion(
                text=answer.text,
                model_id=answer.model_id,
                usage=Usage(
                    input_tokens=answer.input_tokens,
                    output_tokens=answer.output_tokens,
                    cache_creation_input_tokens=0,
                    cache_read_input_tokens=0,
                    batch=True,
                    batch_id=batch_id,
                    request_id=None,
                ),
                stop_reason=answer.stop_reason,
            )
        return results


# ── AnthropicClient (decisions.vocabulary_pinned / decisions.factory_choice) ─


#: How long the live client waits for one batch to end: the Batch API's own
#: 24-hour window. Slice 9 job 2 (2026-09-14) died when a 6-request P3 batch
#: outlasted the previous one-hour ceiling, losing the job's P1-P2 work.
BATCH_MAX_POLL_S: float = 24 * 3600


class AnthropicClient:
    """The real ModelClient, talking to the Anthropic API.

    Never constructs a bare `anthropic.Anthropic()`: every SDK object comes
    from src/tour's bounded factories (decisions.factory_choice), which is
    what keeps an unbounded 600 s x 2-retry default from hanging an ingest
    run for half an hour (see src/tour/anthropic_client.py's docstring, and
    the sweep in tests/test_anthropic_client_bounded.py).

    Two DIFFERENT bounded objects, for two different failure modes:

    * the SYNC object — `anthropic_client.batch_review_client()`, 300 s with
      3 retries: offline review with nothing waiting on it, so a slow
      response should be retried rather than end the run. Never
      `judge_client()` (45 s would kill an Opus P2/P4 call) and never
      `compose_client()` (an interactive ceiling).
    * the batch-SUBMISSION object — `batch_transport.batch_client()`, ZERO
      retries: a hidden SDK retry on a submission could double-submit the
      batch, i.e. double-spend (batch_transport.py:39-53). Its `timeout=None`
      is deliberate, not an oversight — a batch is not attached to a UI
      latency promise, and `poll_batch` bounds the wait itself.

    Both are built lazily, on first use, through a module-attribute lookup
    inside the method: importing src.ingest.llm must never import the SDK
    (AC-21), and a test must be able to monkeypatch either factory.

    Step 6 builds construction (layer (a) of the judge-independence gate,
    shared with MockClient) and `count_tokens()`. Step 7 adds `estimate()`
    (the same `_price_plan` arithmetic as `MockClient`, priced against this
    client's own `count_tokens()`) and `complete()` — the exact pinned
    request shape, empty/truncated refusals, and layer (b) of the judge-
    independence gate. Step 8 adds `complete_batch()`, which runs the whole
    Batch API round through `src.tour.batch_transport`.
    """

    def __init__(
        self,
        events: EventSink,
        *,
        sdk: object | None = None,
        submit_sdk: object | None = None,
        roles: MappingProxyType[str, str] | dict[str, str] = ROLE_MODEL,
        poll_interval_s: float = 10.0,
        max_poll_s: float = BATCH_MAX_POLL_S,
    ) -> None:
        roles_dict = dict(roles)
        _refuse_if_judge_is_author(roles_dict)
        self._sink = events
        self.roles = roles_dict
        self._sdk = sdk
        self._submit_sdk = submit_sdk
        self.poll_interval_s = poll_interval_s
        self.max_poll_s = max_poll_s
        self._estimate_printed = False

    def _get_sdk(self) -> object:
        """The sync/read SDK object: whatever was injected, else the bounded
        offline-review client, built once on first use."""
        if self._sdk is None:
            from src.tour import anthropic_client as _ac

            self._sdk = _ac.batch_review_client()
        return self._sdk

    def _get_submit_sdk(self) -> object:
        """The batch-SUBMISSION SDK object: whatever was injected, else the
        zero-retry submission client, built once on first use."""
        if self._submit_sdk is None:
            from src.tour import batch_transport as _bt

            self._submit_sdk = _bt.batch_client()
        return self._submit_sdk

    def count_tokens(self, model_id: str, text: str) -> int:
        """Provider token count for `text` under `model_id`, from the API's
        own count_tokens endpoint (so estimate() prices real counts, not a
        heuristic)."""
        sdk = self._get_sdk()
        counted = sdk.messages.count_tokens(  # type: ignore[attr-defined]
            model=model_id, messages=[{"role": "user", "content": text}]
        )
        return counted.input_tokens

    def estimate(
        self,
        units: Sequence[str],
        plan: Sequence[PhaseCall],
        *,
        prices: MappingProxyType[str, tuple[float, float]] = PRICES_USD_PER_MTOK,
    ) -> CostEstimate:
        """Price `plan` over `units` using this client's own count_tokens
        (the real provider endpoint) via the shared `_price_plan` helper —
        identical arithmetic to MockClient.estimate() — emit the result to
        the EventSink as 'cost_estimate', and arm this instance's estimate
        gate."""
        result = _price_plan(self.roles, self.count_tokens, units, plan, prices)
        self._sink("cost_estimate", result.as_dict())
        self._estimate_printed = True
        return result

    def _message_kwargs(
        self, role: str, prompt: str, schema: dict | None, max_tokens: int
    ) -> dict[str, object]:
        """The EXACT pinned request body (decisions.sdk_shapes): {model,
        max_tokens, messages: [{role: 'user', content: prompt}]} plus
        output_config iff `schema` is not None — no thinking, no system, no
        prefill, no output_format. Shared by the sync path (the kwargs of
        sdk.messages.create) and the batch path (each unit's `params`) so
        one transport's shape can never drift from the other's."""
        kwargs: dict[str, object] = {
            "model": self.roles[role],
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if schema is not None:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        return kwargs

    def complete(
        self,
        role: str,
        prompt: str,
        schema: dict | None,
        *,
        phase: str,
        max_tokens: int,
    ) -> Completion:
        """One sync-path completion against the real Anthropic API,
        enforcing the same decisions.call_order as MockClient.complete():
        (1) role/phase/transport/phase-role validation ->
        UnknownRole/UnknownPhase/WrongTransport/PhaseRoleMismatch, zero
        calls; (2) the per-instance estimate gate -> EstimateNotPrinted,
        zero calls; (3) a request built to the EXACT pinned shape
        (decisions.sdk_shapes): {model, max_tokens, messages: [{role:
        'user', content: prompt}]} plus output_config iff schema is not
        None — no thinking, no system, no prefill, no output_format; (4)
        post-response checks -> EmptyCompletion (empty/whitespace text,
        concatenated from the response's type=='text' content blocks),
        TruncatedCompletion (stop_reason == 'max_tokens', partial text
        attached), JudgeIsAuthor (model_id from response.model, via
        same_model against the author's configured model id)."""
        if role not in ROLE_MODEL:
            raise UnknownRole(f"unknown role {role!r}")
        if phase not in PHASE_ROLE:
            raise UnknownPhase(f"unknown phase {phase!r}")
        if phase in BATCH_PHASES:
            raise WrongTransport(f"phase {phase!r} is a batch phase; use complete_batch()")
        if PHASE_ROLE[phase] != role:
            raise PhaseRoleMismatch(
                f"phase {phase!r} is owned by role {PHASE_ROLE[phase]!r}, not {role!r}"
            )
        if not self._estimate_printed:
            raise EstimateNotPrinted(
                "estimate() must be called on this client before complete()"
            )

        sdk = self._get_sdk()
        response = sdk.messages.create(  # type: ignore[attr-defined]
            **self._message_kwargs(role, prompt, schema, max_tokens)
        )

        content = getattr(response, "content", None) or []
        text = "".join(
            getattr(block, "text", "") or ""
            for block in content
            if getattr(block, "type", None) == "text"
        )
        model_id = response.model  # type: ignore[attr-defined]
        stop_reason = response.stop_reason  # type: ignore[attr-defined]

        if not text.strip():
            raise EmptyCompletion(f"role {role!r} phase {phase!r} returned empty text")
        if stop_reason == "max_tokens":
            raise TruncatedCompletion(
                f"role {role!r} phase {phase!r} truncated at max_tokens", text
            )
        if role in JUDGE_ROLES:
            author_model = self.roles.get("author")
            if author_model is not None and same_model(model_id, author_model):
                raise JudgeIsAuthor(
                    f"role {role!r} answered with the author's model id "
                    f"({model_id!r} matches {author_model!r})"
                )

        usage = response.usage  # type: ignore[attr-defined]
        return Completion(
            text=text,
            model_id=model_id,
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0)
                or 0,
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                batch=False,
                batch_id=None,
                request_id=getattr(response, "id", None),
            ),
            stop_reason=stop_reason,
        )

    def complete_batch(
        self,
        role: str,
        prompts: Sequence[tuple[str, str]],
        schema: dict | None,
        *,
        phase: str,
        max_tokens: int,
    ) -> dict[str, Completion | BatchFailure]:
        """One Batch API round against the real provider, enforcing the same
        decisions.call_order as MockClient.complete_batch(): (1)
        role/phase/transport/phase-role validation, then the Batch API's own
        custom_id contract (a raw ValueError for a bad pattern or a
        duplicate — refused here so it costs $0 rather than a 400 after the
        requests are built); (2) the per-instance estimate gate ->
        EstimateNotPrinted; (3) the round itself, entirely through
        src.tour.batch_transport: submit_batch() with the ZERO-RETRY
        submission client and one `_message_kwargs()` body per unit, the
        'batch_submitted' event, poll_batch() (bounded by this client's
        poll_interval_s/max_poll_s) and collect_results(); (4) the
        post-response checks on every succeeded unit -> TruncatedCompletion,
        JudgeIsAuthor.

        The result is built by iterating the SUBMITTED prompts, never the
        results stream: a unit the provider never mentions surfaces as
        BatchFailure('missing') instead of vanishing, and an errored,
        canceled or expired unit keeps its provider error text.

        EmptyCompletion is not reachable on this path: an empty-text
        SUCCEEDED unit raises ValueError from inside collect_results (via
        certification_provider._response_text) for the whole collection
        first. That is src/tour behaviour, disclosed in
        decisions.call_order / out_of_scope and not fixed by this slice; the
        check is kept so the two clients read alike and so it still holds if
        src/tour ever stops raising.
        """
        if role not in ROLE_MODEL:
            raise UnknownRole(f"unknown role {role!r}")
        if phase not in PHASE_ROLE:
            raise UnknownPhase(f"unknown phase {phase!r}")
        if phase not in BATCH_PHASES:
            raise WrongTransport(f"phase {phase!r} is not a batch phase; use complete()")
        if PHASE_ROLE[phase] != role:
            raise PhaseRoleMismatch(
                f"phase {phase!r} is owned by role {PHASE_ROLE[phase]!r}, not {role!r}"
            )

        seen_ids: set[str] = set()
        for custom_id, _prompt in prompts:
            if not _CUSTOM_ID_PATTERN.fullmatch(custom_id):
                raise ValueError(
                    f"custom_id {custom_id!r} violates the Batch API pattern "
                    "^[a-zA-Z0-9_-]{1,64}$"
                )
            if custom_id in seen_ids:
                raise ValueError(f"duplicate custom_id {custom_id!r}")
            seen_ids.add(custom_id)

        if not self._estimate_printed:
            raise EstimateNotPrinted(
                "estimate() must be called on this client before complete_batch()"
            )

        from src.tour import batch_transport as _bt

        requests = [
            (custom_id, self._message_kwargs(role, prompt, schema, max_tokens))
            for custom_id, prompt in prompts
        ]
        batch = _bt.submit_batch(requests, client=self._get_submit_sdk())
        batch_id = batch.id
        self._sink(
            "batch_submitted",
            {"phase": phase, "role": role, "batch_id": batch_id, "count": len(requests)},
        )
        def heartbeat(batch: object, elapsed_s: float) -> None:
            counts = getattr(batch, "request_counts", None)
            self._sink(
                "batch_polling",
                {
                    "phase": phase,
                    "role": role,
                    "batch_id": batch_id,
                    "elapsed_s": round(elapsed_s, 1),
                    "processing_status": getattr(batch, "processing_status", None),
                    "request_counts": {
                        name: getattr(counts, name, 0)
                        for name in ("processing", "succeeded", "errored", "canceled", "expired")
                    },
                },
            )

        _bt.poll_batch(
            batch_id,
            client=self._get_sdk(),
            poll_interval_s=self.poll_interval_s,
            max_poll_s=self.max_poll_s,
            on_poll=heartbeat,
        )
        collected = _bt.collect_results(batch_id, client=self._get_sdk())

        author_model = self.roles.get("author")
        results: dict[str, Completion | BatchFailure] = {}
        for custom_id, _prompt in prompts:
            unit = collected.get(custom_id)
            if unit is None:
                results[custom_id] = BatchFailure(
                    custom_id=custom_id,
                    result_type="missing",
                    error_message=None,
                    batch_id=batch_id,
                )
                continue
            response = unit.response
            if unit.result_type != "succeeded" or response is None:
                results[custom_id] = BatchFailure(
                    custom_id=custom_id,
                    result_type=unit.result_type,  # type: ignore[arg-type]
                    error_message=unit.error_message,
                    batch_id=batch_id,
                )
                continue

            text = response.body.decode("utf-8")
            model_id = response.model
            stop_reason = response.stop_reason
            if not text.strip():
                raise EmptyCompletion(
                    f"role {role!r} phase {phase!r} custom_id {custom_id!r} returned "
                    "empty text"
                )
            if stop_reason == "max_tokens":
                raise TruncatedCompletion(
                    f"role {role!r} phase {phase!r} custom_id {custom_id!r} truncated "
                    "at max_tokens",
                    text,
                )
            if (
                role in JUDGE_ROLES
                and author_model is not None
                and same_model(model_id, author_model)
            ):
                raise JudgeIsAuthor(
                    f"role {role!r} custom_id {custom_id!r} answered with the "
                    f"author's model id ({model_id!r} matches {author_model!r})"
                )

            results[custom_id] = Completion(
                text=text,
                model_id=model_id,
                usage=Usage(
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cache_creation_input_tokens=response.cache_creation_input_tokens,
                    cache_read_input_tokens=response.cache_read_input_tokens,
                    batch=True,
                    batch_id=batch_id,
                    request_id=response.provider_request_id,
                ),
                stop_reason=stop_reason,
            )
        return results
