"""Re-author the copied backlog into a review file — never into the corpus.

524 beats across Paris and New York are bodies lifted from their source rather
than authored from it. This rewrites them with `claude-opus-5` and writes the
candidates to `data/{city}/reauthored.json`. **Nothing here touches
`beats.json`.** A human reads the pairs and approves them; only then does
anything reach the corpus, through `beats_io.commit`.

The entailment gate runs, but it does NOT decide. It false-negatives on
paraphrase by construction — it was calibrated for compose, where sentences are
built up FROM claims, and re-authoring does the opposite — so its flags are
stored as a **sort key**: the most-doubted rewrites reach the reviewer first.
Using it as a verdict would refuse good prose and teach the reviewer to ignore it.

The run is resumable. 524 paid calls is roughly $11, and a crash at beat 400 must
not buy the first 400 again, so every completed record is flushed to the output
file and skipped on the next run.

Run as:
    ONDOWAY_DEMO_APPROVE=1 uv run python scripts/reauthor_run.py --city paris
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.corpus_report import VERBATIM_THRESHOLD, load_city_beats, verbatim_ratio
from scripts.reauthor_preview import (
    REAUTHOR_MODEL,
    grounding_claims,
    is_excluded,
    reauthor_request,
    sentences,
)

#: Concurrent writers. Matches the pool `scripts/coverage_calibrate.py` already
#: uses for its paid sweep; 524 sequential Opus calls would take over two hours.
DEFAULT_WORKERS = 8

_REPO_ROOT = Path(__file__).resolve().parent.parent


def output_path(city_slug: str, *, data_dir: Path | None = None) -> Path:
    """Where candidates land. Beside the corpus file, never it."""
    root = data_dir if data_dir is not None else _REPO_ROOT / "data"
    return root / city_slug / "reauthored.json"


def already_done(records: list[dict]) -> set[str]:
    """Beat ids already bought, so a resumed run does not pay for them twice."""
    return {r["beat_id"] for r in records if r.get("beat_id")}


def pending_beats(beats: list[dict], done: set[str]) -> list[dict]:
    """The copied beats still owed a rewrite.

    A beat with no `source_passage` is skipped: it is untraceable, a different
    defect with a different remedy, and there is nothing to re-ground it against.
    """
    pending = []
    for beat in beats:
        if beat.get("beat_id") in done:
            continue
        source = (beat.get("source_passage") or "").strip()
        if not source:
            continue
        if verbatim_ratio(beat.get("script_body") or "", source) < VERBATIM_THRESHOLD:
            continue
        pending.append(beat)
    return pending


def make_record(beat: dict, *, rewritten: str, ungrounded: list[str]) -> dict[str, Any]:
    """One reviewable candidate: both bodies, the source, and what was doubted."""
    source = (beat.get("source_passage") or "").strip()
    body_before = beat.get("script_body") or ""
    return {
        "beat_id": beat.get("beat_id", ""),
        "poi_name": beat.get("poi_name", ""),
        "source_passage": source,
        "body_before": body_before,
        "body_after": rewritten,
        "ratio_before": round(verbatim_ratio(body_before, source), 3),
        "ratio_after": round(verbatim_ratio(rewritten, source), 3),
        "ungrounded": ungrounded,
        "flags": len(ungrounded),
        "model": REAUTHOR_MODEL,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def review_order(records: list[dict]) -> list[dict]:
    """Most-doubted first, then most-still-copied — the order a human should read."""
    return sorted(records, key=lambda r: (-r.get("flags", 0), -r.get("ratio_after", 0.0)))


def _rewrite_one(beat: dict, client: Any, checker: Any) -> dict[str, Any]:
    source = (beat.get("source_passage") or "").strip()
    response = client.messages.create(
        **reauthor_request(source=source, body=beat.get("script_body") or "")
    )
    rewritten = "".join(
        getattr(b, "text", "") for b in (getattr(response, "content", []) or [])
    ).strip()
    claims = grounding_claims(beat)
    ungrounded = (
        [s for s in sentences(rewritten) if not checker.entails(claims, s)] if claims else []
    )
    return make_record(beat, rewritten=rewritten, ungrounded=ungrounded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", default="paris", help="City slug under data/.")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N beats (0 = all).")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args(argv)

    if is_excluded(args.city):
        print(f"✗ {args.city} is excluded: its beats are junk data, not a backlog.")
        return 2
    if os.getenv("ONDOWAY_DEMO_APPROVE") != "1":
        print("REFUSED: this run spends money; set ONDOWAY_DEMO_APPROVE=1.", file=sys.stderr)
        return 3

    from dotenv import load_dotenv

    from src.tour.anthropic_client import compose_client
    from src.tour.verify import HaikuFaithfulnessChecker

    load_dotenv()

    out = output_path(args.city)
    records = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else []
    pending = pending_beats(load_city_beats(args.city), already_done(records))
    if args.limit:
        pending = pending[: args.limit]

    print(f"{args.city}: {len(records)} already written, {len(pending)} to rewrite now.")
    if not pending:
        return 0

    client = compose_client()
    checker = HaikuFaithfulnessChecker()
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for record in pool.map(lambda b: _rewrite_one(b, client, checker), pending):
            records.append(record)
            done += 1
            # Flush every record: the next run resumes from whatever survived.
            out.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
            if done % 25 == 0 or done == len(pending):
                print(f"  {done}/{len(pending)}")

    flagged = sum(1 for r in records if r.get("flags"))
    still_copied = sum(1 for r in records if r.get("ratio_after", 0) >= VERBATIM_THRESHOLD)
    print(
        f"✓ {len(records)} candidates in {out}\n"
        f"  {flagged} carry at least one doubted sentence (they sort first for review)\n"
        f"  {still_copied} are still >=70% copied after the rewrite"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
