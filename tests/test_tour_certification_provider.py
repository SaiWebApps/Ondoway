"""Hostile checks for the real zero-retry certification provider boundary."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import src.tour.certification_provider as provider_module
from src.tour.certification_provider import (
    AnthropicCertificationProvider,
    CallPurpose,
    ProviderCancellationScope,
)


class _Messages:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def create(self, **request: object) -> object:
        self.calls.append(request)
        return self.response


class _Client:
    def __init__(self, response: object) -> None:
        self.messages = _Messages(response)


def _response(
    *,
    text: str = '{"ok":true}',
    stop_reason: str = "end_turn",
    usage: object | None = None,
    model: str = "frozen-model",
) -> object:
    return SimpleNamespace(
        id="msg-one",
        model=model,
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)],
        usage=usage or SimpleNamespace(input_tokens=11, output_tokens=7),
    )


def _provider(response: object) -> tuple[AnthropicCertificationProvider, _Client, _Client]:
    compose = _Client(response)
    judge = _Client(response)
    return (
        AnthropicCertificationProvider(compose_client=compose, judge_client=judge),
        compose,
        judge,
    )


def test_physical_clients_are_lazy_and_purpose_specific(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _response()
    compose = _Client(response)
    judge = _Client(response)
    constructed: list[str] = []
    monkeypatch.setattr(
        provider_module,
        "certification_compose_client",
        lambda: constructed.append("compose") or compose,
    )
    monkeypatch.setattr(
        provider_module,
        "certification_judge_client",
        lambda: constructed.append("judge") or judge,
    )

    provider = AnthropicCertificationProvider()
    assert constructed == []

    provider.invoke(
        purpose=CallPurpose.COMPOSE,
        unit_id="stop:0",
        request={"model": "frozen-model"},
    )

    assert constructed == ["compose"]
    assert compose.messages.calls == [{"model": "frozen-model"}]
    assert judge.messages.calls == []


def test_routes_exact_mapping_to_one_purpose_specific_physical_call() -> None:
    provider, compose, judge = _provider(_response())
    request = {"model": "frozen-model", "messages": [{"role": "user"}]}

    result = provider.invoke(
        purpose=CallPurpose.COMPOSE,
        unit_id="stop:0",
        request=request,
    )

    assert compose.messages.calls == [request]
    assert judge.messages.calls == []
    assert result.body == b'{"ok":true}'
    assert (result.input_tokens, result.output_tokens) == (11, 7)
    assert result.provider_request_id == "msg-one"
    assert result.latency_ms >= 0


@pytest.mark.parametrize(
    "purpose",
    [
        CallPurpose.CALIBRATION,
        CallPurpose.FACT_REVIEW,
        CallPurpose.ENJOY_REVIEW,
    ],
)
def test_every_review_purpose_uses_only_the_zero_retry_judge_boundary(purpose: CallPurpose) -> None:
    provider, compose, judge = _provider(_response(model="judge"))
    provider.invoke(purpose=purpose, unit_id="unit", request={"model": "judge"})
    assert compose.messages.calls == []
    assert judge.messages.calls == [{"model": "judge"}]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_response(text="   "), "empty"),
        (_response(usage=SimpleNamespace(input_tokens=None, output_tokens=1)), "not integral"),
    ],
)
def test_incomplete_physical_response_fails_without_an_adapter_retry(
    response: object, message: str
) -> None:
    provider, compose, _judge = _provider(response)
    with pytest.raises(ValueError, match=message):
        provider.invoke(
            purpose=CallPurpose.COMPOSE,
            unit_id="stop:0",
            request={"model": "compose"},
        )
    assert compose.messages.calls == [{"model": "compose"}]


def test_truncated_physical_response_preserves_body_usage_and_provider_identity() -> None:
    provider, compose, _judge = _provider(
        _response(text='{"partial":true}', stop_reason="max_tokens")
    )

    result = provider.invoke(
        purpose=CallPurpose.COMPOSE,
        unit_id="stop:0",
        request={"model": "frozen-model"},
    )

    assert compose.messages.calls == [{"model": "frozen-model"}]
    assert result.body == b'{"partial":true}'
    assert (result.input_tokens, result.output_tokens) == (11, 7)
    assert result.provider_request_id == "msg-one"
    assert result.stop_reason == "max_tokens"


@pytest.mark.parametrize("model", ["", "different-model"])
def test_missing_or_wrong_physical_response_model_is_rejected(model: str) -> None:
    provider, compose, _judge = _provider(_response(model=model))
    with pytest.raises(ValueError, match="model"):
        provider.invoke(
            purpose=CallPurpose.COMPOSE,
            unit_id="stop:0",
            request={"model": "frozen-model"},
        )
    assert compose.messages.calls == [{"model": "frozen-model"}]


def test_explicit_job_cancellation_closes_the_physical_provider_stream() -> None:
    entered = threading.Event()
    closed = threading.Event()

    class SlowStream:
        def __enter__(self):
            entered.set()
            return self

        def __exit__(self, *_args):
            self.close()

        def close(self) -> None:
            closed.set()

        def get_final_message(self):
            assert closed.wait(timeout=2)
            raise RuntimeError("stream closed")

    class StreamingMessages:
        def stream(self, **_request):
            return SlowStream()

    client = SimpleNamespace(messages=StreamingMessages())
    scope = ProviderCancellationScope()
    provider = AnthropicCertificationProvider(
        compose_client=client,
        judge_client=client,
        cancellation_scope=scope,
    )
    errors: list[Exception] = []

    def invoke() -> None:
        try:
            provider.invoke(
                purpose=CallPurpose.COMPOSE,
                unit_id="stop:0",
                request={"model": "frozen-model"},
            )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=invoke)
    thread.start()
    assert entered.wait(timeout=2)
    scope.cancel()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert closed.is_set()
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
