"""Recover a finished Batch API round — `make ingest-batch BATCH=<id>`. $0.

Slice 9's first paid job (2026-09-12) had its P1 answer truncated at the
output cap; the runner held the unit and kept only the exception's
message, so the answer already paid for — and the usage the run's meter
never saw — lived only in the batch. This reads the round back (results
stay retrievable for 29 days), prints per request how it stopped, the
model, the tokens and the priced cost, and how many claim items a P1
answer carried before it broke off, and writes the whole round to
`{out}/{batch_id}.json`.

    make ingest-batch BATCH=msgbatch_013YWoWeV8u4jNt5uzwF5C7k
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ingest import llm
from src.tour.batch_transport import collect_results

DEFAULT_OUT = ROOT / "data-ingest" / "batches"


def count_claim_items(text: str) -> tuple[int, bool]:
    """How many complete `{text, kind, span}` items a P1 answer carries, and
    whether the JSON was cut off. A truncated answer is read item by item
    from the `claims` array until the decoder fails."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        claims = parsed.get("claims") if isinstance(parsed, dict) else None
        return (len(claims) if isinstance(claims, list) else 0), False
    marker = '"claims"'
    start = text.find(marker)
    if start < 0:
        return 0, True
    start = text.find("[", start)
    if start < 0:
        return 0, True
    decoder = json.JSONDecoder()
    index = start + 1
    count = 0
    while True:
        while index < len(text) and text[index] in " \t\r\n,":
            index += 1
        try:
            _item, index = decoder.raw_decode(text, index)
        except (json.JSONDecodeError, ValueError):
            return count, True
        count += 1


def _price(model: str, in_tokens: int, out_tokens: int) -> float | None:
    for key, (price_in, price_out) in llm.PRICES_USD_PER_MTOK.items():
        if llm.same_model(model, key):
            usd = (in_tokens / 1_000_000) * price_in + (out_tokens / 1_000_000) * price_out
            return usd * llm.BATCH_DISCOUNT
    return None


def _live_count_tokens():
    """The provider's free count_tokens endpoint, under the author's
    configured model (the answer's own model id is dated and unpriced)."""
    client = llm.AnthropicClient(lambda _kind, _payload: None)
    return client.count_tokens


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch", required=True, help="the msgbatch_... id")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--text-tokens",
        action="store_true",
        help="count each answer's text with count_tokens ($0) so non-text (thinking) = out - text",
    )
    args = parser.parse_args(argv)
    count_tokens = _live_count_tokens() if args.text_tokens else None

    results = collect_results(args.batch)
    saved: dict[str, Any] = {}
    total_usd = 0.0
    for custom_id, unit in sorted(results.items()):
        response = unit.response
        if response is None:
            print(f"{custom_id} {unit.result_type}: {unit.error_message or '-'}")
            saved[custom_id] = {"result_type": unit.result_type, "error": unit.error_message}
            continue
        text = response.body.decode("utf-8")
        in_tokens = (
            response.input_tokens
            + response.cache_creation_input_tokens
            + response.cache_read_input_tokens
        )
        usd = _price(response.model, in_tokens, response.output_tokens)
        total_usd += usd or 0.0
        claims, partial = count_claim_items(text)
        text_tokens = count_tokens(llm.ROLE_MODEL["author"], text) if count_tokens else None
        split = (
            f" text_tokens={text_tokens} nontext_tokens={response.output_tokens - text_tokens}"
            if text_tokens is not None
            else ""
        )
        print(
            f"{custom_id} {unit.result_type} stop={response.stop_reason} model={response.model} "
            f"in={in_tokens} out={response.output_tokens} chars={len(text)} "
            f"usd={'unpriced' if usd is None else f'{usd:.4f}'} "
            f"claims_parsed={claims}{' (truncated JSON: partial)' if partial else ''}{split}"
        )
        saved[custom_id] = {
            "result_type": unit.result_type,
            "stop_reason": response.stop_reason,
            "model": response.model,
            "input_tokens": in_tokens,
            "output_tokens": response.output_tokens,
            "usd": usd,
            "claims_parsed": claims,
            "partial": partial,
            "text_tokens": text_tokens,
            "text": text,
        }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{args.batch}.json"
    path.write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"batch {args.batch}: {len(results)} request(s), usd={total_usd:.4f}, saved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
