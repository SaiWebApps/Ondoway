"""Offline checks for the v2 batch receipt schema and cross-version validation."""

from __future__ import annotations

import hashlib
import json

import pytest

from src.tour.batch_transport import (
    BatchUnitResult,
    build_batch_receipt,
    load_batch_submission,
    persist_batch_submission,
    submit_batch,
    validate_receipt,
)
from src.tour.certification_provider import PhysicalProviderResponse


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _succeeded_unit_result() -> BatchUnitResult:
    response = PhysicalProviderResponse(
        body=b'{"sentences":[]}',
        input_tokens=11,
        output_tokens=7,
        latency_ms=0,
        model="frozen-model",
        provider_request_id="msg-one",
    )
    return BatchUnitResult(
        custom_id="stop:0",
        result_type="succeeded",
        response=response,
        batch_id="msgbatch_01",
    )


def _receipt(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "unit_result": _succeeded_unit_result(),
        "request_id": "req-0",
        "request_sha256": "a" * 64,
        "response_sha256": "b" * 64,
        "parsed_payload_sha256": "c" * 64,
        "poi_name": "Louvre",
        "stop_index": 0,
        "raw_response": '{"sentences":[]}',
    }
    kwargs.update(overrides)
    return build_batch_receipt(**kwargs)


def test_build_batch_receipt_produces_valid_v2() -> None:
    receipt = _receipt()

    assert receipt["schema_version"] == "ondoway-text-candidate-stop-v2"
    assert receipt["batch_id"] == "msgbatch_01"
    assert receipt["result_type"] == "succeeded"
    assert "latency_ms" not in receipt
    core = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    assert receipt["receipt_sha256"] == _canonical_sha256(core)


def test_validate_receipt_accepts_v1() -> None:
    core = {
        "schema_version": "ondoway-text-candidate-stop-v1",
        "stop_index": 0,
        "poi_name": "Louvre",
        "request_id": "req-0",
        "request_sha256": "a" * 64,
        "response_sha256": "b" * 64,
        "parsed_payload_sha256": "c" * 64,
        "provider_request_id": "msg-one",
        "model": "frozen-model",
        "input_tokens": 11,
        "output_tokens": 7,
        "latency_ms": 842,
        "raw_response": '{"sentences":[]}',
    }
    receipt = {**core, "receipt_sha256": _canonical_sha256(core)}

    validate_receipt(receipt)


def test_validate_receipt_accepts_v2() -> None:
    validate_receipt(_receipt())


def test_validate_receipt_rejects_tampered_hash() -> None:
    tampered = {**_receipt(), "poi_name": "Eiffel Tower"}

    with pytest.raises(ValueError, match="receipt_sha256"):
        validate_receipt(tampered)


def test_validate_receipt_rejects_unknown_schema() -> None:
    core = {"schema_version": "ondoway-text-candidate-stop-v99", "stop_index": 0}
    receipt = {**core, "receipt_sha256": _canonical_sha256(core)}

    with pytest.raises(ValueError, match="schema_version"):
        validate_receipt(receipt)


def test_persist_and_load_batch_submission(tmp_path) -> None:
    path = tmp_path / "submission.json"
    persist_batch_submission(
        path,
        batch_id="msgbatch_01",
        custom_ids=["stop:0", "stop:1"],
        plan_sha256="d" * 64,
    )

    loaded = load_batch_submission(path)

    assert loaded is not None
    assert loaded["schema_version"] == "ondoway-batch-submission-v1"
    assert loaded["batch_id"] == "msgbatch_01"
    assert loaded["custom_ids"] == ["stop:0", "stop:1"]
    assert loaded["plan_sha256"] == "d" * 64
    assert isinstance(loaded["submitted_at"], str)

    with pytest.raises(FileExistsError):
        persist_batch_submission(
            path,
            batch_id="msgbatch_02",
            custom_ids=["stop:0"],
            plan_sha256="d" * 64,
        )


def test_load_batch_submission_returns_none_for_missing(tmp_path) -> None:
    assert load_batch_submission(tmp_path / "nonexistent.json") is None


def test_submit_batch_refuses_custom_ids_the_api_would_reject() -> None:
    """The Batch API's custom_id alphabet is [a-zA-Z0-9_-]{1,64}. The first live
    submission was refused with a 400 for a colon a stub client had accepted, so
    the contract is enforced locally: a bad id is a $0 ValueError before any
    request is built or any client touched."""
    for bad in ("case:0", "über-stop", "x" * 65, ""):
        with pytest.raises(ValueError, match="custom_id"):
            submit_batch([(bad, {})], client=object())


def test_load_batch_submission_rejects_wrong_schema(tmp_path) -> None:
    path = tmp_path / "submission.json"
    path.write_text(json.dumps({"schema_version": "wrong-version"}), encoding="utf-8")

    with pytest.raises(ValueError, match="schema_version"):
        load_batch_submission(path)


def test_poll_batch_reports_every_poll_to_the_caller() -> None:
    """Slice 9 (2026-09-12): a batch phase polls for up to an hour in
    silence, so a healthy job and a hung one look identical from outside.
    `on_poll` is called on every poll — the in-progress ones and the final
    'ended' one — with the batch object and the seconds elapsed, so the
    caller can print a heartbeat. Absent, nothing changes."""
    import types

    from src.tour import batch_transport as bt

    statuses = iter(["in_progress", "in_progress", "ended"])
    retrieved: list[str] = []

    class _Batches:
        def retrieve(self, batch_id):
            retrieved.append(batch_id)
            return types.SimpleNamespace(id=batch_id, processing_status=next(statuses))

    client = types.SimpleNamespace(messages=types.SimpleNamespace(batches=_Batches()))
    polls: list[tuple[str, float]] = []

    batch = bt.poll_batch(
        "msgbatch_x",
        client=client,
        poll_interval_s=0,
        on_poll=lambda b, elapsed: polls.append((b.processing_status, elapsed)),
    )

    assert batch.processing_status == "ended"
    assert retrieved == ["msgbatch_x"] * 3
    assert [status for status, _e in polls] == ["in_progress", "in_progress", "ended"]
    assert all(elapsed >= 0 for _s, elapsed in polls)
    assert polls[0][1] <= polls[1][1] <= polls[2][1]


def test_a_failing_poll_callback_never_stops_the_poll(capsys) -> None:
    """The judge on the heartbeat: the callback ends in a file write in the
    live CLI, so an OSError there would abort a batch that is already
    submitted and billed. Observability on a paid path must never be
    load-bearing: a raising `on_poll` is reported on one line and the poll
    goes on to return the ended batch exactly as if no callback existed."""
    import types

    from src.tour import batch_transport as bt

    statuses = iter(["in_progress", "ended"])

    class _Batches:
        def retrieve(self, batch_id):
            return types.SimpleNamespace(id=batch_id, processing_status=next(statuses))

    client = types.SimpleNamespace(messages=types.SimpleNamespace(batches=_Batches()))
    calls: list[str] = []

    def failing(batch, _elapsed):
        calls.append(batch.processing_status)
        raise OSError("disk full")

    batch = bt.poll_batch("msgbatch_x", client=client, poll_interval_s=0, on_poll=failing)

    assert batch.processing_status == "ended"
    assert calls == ["in_progress", "ended"]
    out = capsys.readouterr().out
    assert "on_poll" in out and "disk full" in out


def _api_errors():
    """Real SDK exception instances, one per class the poll must tell apart."""
    import anthropic
    import httpx

    request = httpx.Request("GET", "https://api.anthropic.com/v1/messages/batches/msgbatch_x")

    def status(cls, code):
        return cls("status", response=httpx.Response(code, request=request), body=None)

    return {
        "connection": anthropic.APIConnectionError(request=request),
        "timeout": anthropic.APITimeoutError(request=request),
        "server": status(anthropic.InternalServerError, 500),
        "rate_limit": status(anthropic.RateLimitError, 429),
        "auth": status(anthropic.AuthenticationError, 401),
    }


def test_transient_status_check_errors_are_tolerated_up_to_a_limit_when_asked(capsys) -> None:
    """Slice 9 job 2 (2026-09-14): with the ingest poll now allowed 24 hours,
    one status check that fails past the SDK's own retries would still end a
    paid job. A caller may ask `poll_batch` to ride out up to N CONSECUTIVE
    transient failures (connection, timeout, 5xx, 429): each is reported to
    `on_poll_error(exc, consecutive)` and the poll goes on; a successful check
    resets the count; one past N raises it; a non-transient error (auth)
    raises at once; and the default (0) leaves every caller as it was."""
    import types

    from src.tour import batch_transport as bt

    errors = _api_errors()

    def client_answering(*script):
        answers = iter(script)

        class _Batches:
            def retrieve(self, batch_id):
                answer = next(answers)
                if isinstance(answer, Exception):
                    raise answer
                return types.SimpleNamespace(id=batch_id, processing_status=answer)

        return types.SimpleNamespace(messages=types.SimpleNamespace(batches=_Batches()))

    seen: list[tuple[str, int]] = []
    client = client_answering(
        errors["connection"], errors["timeout"], "in_progress",
        errors["server"], errors["rate_limit"], "ended",
    )
    batch = bt.poll_batch(
        "msgbatch_x", client=client, poll_interval_s=0, max_consecutive_retrieve_errors=2,
        on_poll_error=lambda exc, n: seen.append((type(exc).__name__, n)),
    )
    assert batch.processing_status == "ended"
    assert seen == [
        ("APIConnectionError", 1), ("APITimeoutError", 2),
        ("InternalServerError", 1), ("RateLimitError", 2),
    ]

    client = client_answering(errors["server"], errors["server"], errors["server"], "ended")
    with pytest.raises(type(errors["server"])):
        bt.poll_batch(
            "msgbatch_x", client=client, poll_interval_s=0, max_consecutive_retrieve_errors=2
        )

    client = client_answering(errors["auth"], "ended")
    with pytest.raises(type(errors["auth"])):
        bt.poll_batch(
            "msgbatch_x", client=client, poll_interval_s=0, max_consecutive_retrieve_errors=5
        )

    client = client_answering(errors["connection"], "ended")
    with pytest.raises(type(errors["connection"])):
        bt.poll_batch("msgbatch_x", client=client, poll_interval_s=0)


def test_error_retries_back_off_when_asked_and_a_good_check_restores_the_pace() -> None:
    """Judge, checkpoint 11: six tolerated errors at a 10 s pace cover about
    85 s of a fast-failing outage (DNS, refused connection, an instant 503).
    With `retrieve_error_backoff_s` the wait after the nth consecutive error
    is backoff * 2**(n-1), so the same budget spans a real outage; after a
    good check the next wait is the ordinary `poll_interval_s` again and the
    backoff starts over. Unset, errors wait `poll_interval_s` as before."""
    import types

    from src.tour import batch_transport as bt

    error = _api_errors()["connection"]

    def run(script, **kwargs):
        clock = types.SimpleNamespace(now=0.0, slept=[])
        answers = iter(script)

        class _Batches:
            def retrieve(self, batch_id):
                answer = next(answers)
                if isinstance(answer, Exception):
                    raise answer
                return types.SimpleNamespace(id=batch_id, processing_status=answer)

        def sleep(seconds):
            clock.slept.append(seconds)
            clock.now += seconds

        original = bt.time
        bt.time = types.SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep)
        try:
            bt.poll_batch(
                "msgbatch_x",
                client=types.SimpleNamespace(messages=types.SimpleNamespace(batches=_Batches())),
                poll_interval_s=10, max_poll_s=86_400, **kwargs,
            )
        finally:
            bt.time = original
        return clock.slept

    assert run(
        [error, error, error, "in_progress", error, "ended"],
        max_consecutive_retrieve_errors=6, retrieve_error_backoff_s=30,
    ) == [30, 60, 120, 10, 30]
    assert run(
        [error, error, "ended"], max_consecutive_retrieve_errors=6
    ) == [10, 10]


def test_a_not_found_just_after_submission_is_tolerated_inside_the_grace_window() -> None:
    """Slice 9 job 2 attempt 2 (2026-09-14): the first status check, 169 ms
    after the batch was created, answered 404 not_found and ended a paid job.
    With `not_found_grace_s`, a 404 that early counts as a tolerated error
    (same budget, backoff and report as a transient one); after the window a
    404 means the batch is really gone and raises; the default raises at once."""
    import types

    import anthropic
    import httpx

    from src.tour import batch_transport as bt

    request = httpx.Request("GET", "https://api.anthropic.com/v1/messages/batches/msgbatch_x")
    not_found = anthropic.NotFoundError(
        "Message Batch not found", response=httpx.Response(404, request=request), body=None
    )

    def poll(script, **kwargs):
        clock = types.SimpleNamespace(now=0.0)
        answers = iter(script)

        class _Batches:
            def retrieve(self, batch_id):
                answer = next(answers)
                if isinstance(answer, Exception):
                    raise answer
                return types.SimpleNamespace(id=batch_id, processing_status=answer)

        original = bt.time
        bt.time = types.SimpleNamespace(
            monotonic=lambda: clock.now,
            sleep=lambda seconds: setattr(clock, "now", clock.now + seconds),
        )
        try:
            return bt.poll_batch(
                "msgbatch_x",
                client=types.SimpleNamespace(messages=types.SimpleNamespace(batches=_Batches())),
                poll_interval_s=10, max_poll_s=86_400, **kwargs,
            )
        finally:
            bt.time = original

    seen: list[tuple[str, int]] = []
    batch = poll(
        [not_found, "ended"], max_consecutive_retrieve_errors=6, retrieve_error_backoff_s=30,
        not_found_grace_s=120, on_poll_error=lambda exc, n: seen.append((type(exc).__name__, n)),
    )
    assert batch.processing_status == "ended"
    assert seen == [("NotFoundError", 1)]

    with pytest.raises(anthropic.NotFoundError):
        poll(
            ["in_progress"] * 13 + [not_found, "ended"],
            max_consecutive_retrieve_errors=6, not_found_grace_s=120,
        )

    with pytest.raises(anthropic.NotFoundError):
        poll([not_found, "ended"], max_consecutive_retrieve_errors=6)
