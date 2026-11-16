"""Proves the Anthropic Batch API works with the exact request shape the
certification compose path uses.
"""

from __future__ import annotations

import json
import time

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from src.model_registry import effort_for, model_for, price_per_mtok

PROBE_CUSTOM_ID = "probe-batch-api-v1"
PROBE_MODEL = model_for("batch_probe")
PROBE_EFFORT = effort_for("batch_probe")
PROBE_MAX_TOKENS = 64000
PROBE_SYSTEM_PROMPT = "You are a walking-tour narrator. Respond only with schema-constrained JSON."
PROBE_USER_MESSAGE = (
    "Write one short sentence of spoken tour narration about a historic "
    "fountain. Use source_id probe-beat-1, source_type beat, and stop_idx 0."
)
POLL_INTERVAL_SECONDS = 5
POLL_MAX_ITERATIONS = 120
BATCH_RATE_MULTIPLIER = 0.5

PROBE_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_id": {"type": "string"},
                    "source_type": {"type": "string", "enum": ["beat", "glue"]},
                    "stop_idx": {"type": "integer"},
                },
                "required": ["text", "source_id", "source_type", "stop_idx"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sentences"],
    "additionalProperties": False,
}


def main() -> int:
    client = anthropic.Anthropic()
    params = MessageCreateParamsNonStreaming(
        model=PROBE_MODEL,
        max_tokens=PROBE_MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={
            "format": {"type": "json_schema", "schema": PROBE_SCHEMA},
            **({"effort": PROBE_EFFORT} if PROBE_EFFORT is not None else {}),
        },
        system=[
            {
                "type": "text",
                "text": PROBE_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": PROBE_USER_MESSAGE}],
    )
    batch = client.messages.batches.create(
        requests=[Request(custom_id=PROBE_CUSTOM_ID, params=params)]
    )

    for _ in range(POLL_MAX_ITERATIONS):
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        time.sleep(POLL_INTERVAL_SECONDS)
    else:
        print(
            f"PROBE FAILED: batch {batch.id} did not reach 'ended' within "
            f"{POLL_MAX_ITERATIONS * POLL_INTERVAL_SECONDS} seconds; last "
            f"processing_status was {batch.processing_status!r}"
        )
        return 1

    results = list(client.messages.batches.results(batch.id))
    if len(results) != 1:
        print(f"PROBE FAILED: expected exactly one batch result, got {len(results)}")
        return 1
    individual_response = results[0]
    if individual_response.custom_id != PROBE_CUSTOM_ID:
        print(
            f"PROBE FAILED: result custom_id {individual_response.custom_id!r} "
            f"does not match {PROBE_CUSTOM_ID!r}"
        )
        return 1
    outcome = individual_response.result
    if outcome.type != "succeeded":
        print(f"PROBE FAILED: batch result type was {outcome.type!r}")
        print(json.dumps(outcome.model_dump(mode="json"), indent=2, sort_keys=True))
        return 1

    message = outcome.message
    assert message.model.startswith(PROBE_MODEL), message.model
    assert isinstance(message.stop_reason, str) and message.stop_reason, message.stop_reason
    assert isinstance(message.id, str) and message.id, message.id
    usage = message.usage
    assert isinstance(usage.input_tokens, int) and usage.input_tokens > 0, usage.input_tokens
    assert isinstance(usage.output_tokens, int) and usage.output_tokens > 0, usage.output_tokens
    assert isinstance(usage.cache_creation_input_tokens, int), usage.cache_creation_input_tokens
    assert isinstance(usage.cache_read_input_tokens, int), usage.cache_read_input_tokens

    text_blocks = [block for block in message.content if getattr(block, "type", None) == "text"]
    assert text_blocks, "batch response carried no text content block"
    parsed = json.loads(text_blocks[0].text)
    assert isinstance(parsed, dict), parsed
    sentences = parsed.get("sentences")
    assert isinstance(sentences, list) and len(sentences) >= 1, sentences
    for sentence in sentences:
        assert isinstance(sentence, dict), sentence
        assert isinstance(sentence.get("text"), str) and sentence["text"], sentence
        assert isinstance(sentence.get("source_id"), str) and sentence["source_id"], sentence
        assert sentence.get("source_type") in ("beat", "glue"), sentence
        assert isinstance(sentence.get("stop_idx"), int), sentence

    usd_per_mtok_input, usd_per_mtok_output = price_per_mtok(PROBE_MODEL)
    input_cost_usd = usage.input_tokens * usd_per_mtok_input / 1_000_000
    output_cost_usd = usage.output_tokens * usd_per_mtok_output / 1_000_000
    batch_cost_usd = (input_cost_usd + output_cost_usd) * BATCH_RATE_MULTIPLIER

    print(f"provider_request_id: {message.id}")
    print(f"model: {message.model}")
    print(f"stop_reason: {message.stop_reason}")
    print(f"input_tokens: {usage.input_tokens}")
    print(f"output_tokens: {usage.output_tokens}")
    print(f"cache_creation_input_tokens: {usage.cache_creation_input_tokens}")
    print(f"cache_read_input_tokens: {usage.cache_read_input_tokens}")
    print(f"batch_cost_usd: {batch_cost_usd:.6f}")
    print("PROBE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
