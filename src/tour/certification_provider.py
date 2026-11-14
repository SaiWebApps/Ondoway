"""Zero-retry Anthropic boundary for the durable certification workflow."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from .anthropic_client import (
    certification_compose_client,
    certification_judge_client,
)

if TYPE_CHECKING:
    import anthropic
    from anthropic.types import Message


class CallPurpose(StrEnum):
    CALIBRATION = "calibration"
    COMPOSE = "compose"
    FACT_REVIEW = "fact_review"
    ENJOY_REVIEW = "enjoy_review"


@dataclass(frozen=True)
class PhysicalProviderResponse:
    """Opaque provider response plus provider-reported usage."""

    body: bytes
    input_tokens: int
    output_tokens: int
    latency_ms: int
    model: str
    provider_request_id: str | None = None
    stop_reason: str | None = None
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class CloseableProviderStream(Protocol):
    def close(self) -> None: ...


class ProviderCancellationScope:
    """Own live provider streams without depending on the removed API job layer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False
        self._streams: list[CloseableProviderStream] = []

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def register(self, stream: CloseableProviderStream) -> None:
        with self._lock:
            if not self._cancelled:
                self._streams.append(stream)
                return
        with suppress(Exception):
            stream.close()

    def cancel(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._cancelled = True
            streams = tuple(reversed(self._streams))
            self._streams.clear()
        for stream in streams:
            with suppress(Exception):
                stream.close()


def _response_text(response: Message) -> str:
    blocks = getattr(response, "content", None)
    if not isinstance(blocks, list):
        raise ValueError("Anthropic response omitted its content blocks")
    text_blocks = [
        getattr(block, "text", None) for block in blocks if getattr(block, "type", None) == "text"
    ]
    if not text_blocks or any(not isinstance(value, str) for value in text_blocks):
        raise ValueError("Anthropic response omitted its schema-constrained text")
    text = "".join(text_blocks).strip()
    if not text:
        raise ValueError("Anthropic response contained empty schema-constrained text")
    return text


def _usage(response: Message) -> tuple[int, int, int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        raise ValueError("Anthropic response omitted provider usage")
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        raise ValueError("Anthropic response usage is not integral")
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("Anthropic response usage is negative")
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return input_tokens, output_tokens, cache_creation, cache_read


class AnthropicCertificationProvider:
    """Execute one prepared physical request through explicit zero-retry clients.

    Reservation, call order, request hashing, one-shot closure ownership, and durable
    failure recording belong to :class:`TourCertificationCoordinator`.  This class
    deliberately performs no retry, parsing, repair, fallback, or alternate request.
    """

    def __init__(
        self,
        *,
        compose_client: anthropic.Anthropic | None = None,
        judge_client: anthropic.Anthropic | None = None,
        cancellation_scope: ProviderCancellationScope | None = None,
    ) -> None:
        # Product dependencies may be resolved before provider-free planning and
        # spend reservation. Keep physical clients lazy so construction cannot
        # bypass those boundaries, and never build the unused judge for a compose-
        # only Premium preview. The lock prevents parallel stop workers from
        # constructing duplicate SDK clients.
        self._compose = compose_client
        self._judge = judge_client
        self._client_lock = threading.Lock()
        self._cancellation_scope = cancellation_scope

    def _client_for(self, purpose: CallPurpose) -> anthropic.Anthropic:
        with self._client_lock:
            if purpose is CallPurpose.COMPOSE:
                if self._compose is None:
                    self._compose = certification_compose_client()
                return self._compose
            if self._judge is None:
                self._judge = certification_judge_client()
            return self._judge

    def invoke(
        self,
        *,
        purpose: CallPurpose,
        unit_id: str,
        request: Mapping[str, object],
    ) -> PhysicalProviderResponse:
        if not unit_id:
            raise ValueError("certification provider unit id is empty")
        if purpose not in {
            CallPurpose.CALIBRATION,
            CallPurpose.COMPOSE,
            CallPurpose.FACT_REVIEW,
            CallPurpose.ENJOY_REVIEW,
        }:
            raise ValueError(f"unsupported certification purpose {purpose!r}")
        client = self._client_for(purpose)
        started = time.monotonic()
        if self._cancellation_scope is None:
            response = client.messages.create(**dict(request))
        else:
            if self._cancellation_scope.cancelled:
                raise RuntimeError("certification provider call was cancelled before start")
            manager = client.messages.stream(**dict(request))
            with manager as stream:
                self._cancellation_scope.register(stream)
                response = stream.get_final_message()
        latency_ms = round((time.monotonic() - started) * 1000)
        text = _response_text(response)
        input_tokens, output_tokens, cache_creation, cache_read = _usage(response)
        requested_model = request.get("model")
        response_model = getattr(response, "model", None)
        if not isinstance(requested_model, str) or not requested_model:
            raise ValueError("Anthropic physical request omitted its model")
        if not isinstance(response_model, str) or not response_model:
            raise ValueError("Anthropic response omitted its physical model")
        if response_model != requested_model:
            raise ValueError("Anthropic response model differs from the authorized request")
        request_id = getattr(response, "id", None)
        if request_id is not None and not isinstance(request_id, str):
            raise ValueError("Anthropic provider request id is not text")
        return PhysicalProviderResponse(
            body=text.encode("utf-8"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            model=response_model,
            provider_request_id=request_id,
            stop_reason=getattr(response, "stop_reason", None),
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
        )


__all__ = [
    "AnthropicCertificationProvider",
    "CallPurpose",
    "PhysicalProviderResponse",
    "ProviderCancellationScope",
]
