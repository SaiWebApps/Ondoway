#!/usr/bin/env python
"""One-time migration: populate dedup fields on existing beats.

For each beat in the target beats.json, fill in (when empty):
  - script_body_hash    SHA-256 of normalized script_body
  - city_name           hard-coded "paris" (only Paris is live; multi-city
                        migrations will be run per city when launched)
  - book_slug           parsed from beat_id; sentinel `legacy_unknown` if
                        the beat_id can't be parsed
  - topic_slug          unified_v1 beats already carry this top-level;
                        legacy beats have it parsed out of beat_id
  - source_chunk_slug   resolved by looking the beat's poi_name up in the
                        co-located book-log.json and counting distinct
                        chunk values. Exactly one chunk → that chunk's
                        slug; ≥2 → sentinel `legacy_ambiguous` (never
                        wiped by /beat-wipe, which refuses that sentinel as a
                        target); zero → `legacy_unknown`

Idempotent: never overwrites a non-empty, non-sentinel existing value.
Branches on `_meta.prompt_version`:
  - `unified_v1` → preserve existing top-level `topic_slug`; parse
                   `book_slug` from `{city}_{poi_slug}_{lens}_{book_slug}_{topic_slug}`
  - else        → parse both `book_slug` (suffix) and `topic_slug` (middle)
                   from `{poi_slug}_{lens}_{topic_slug}_{book_slug}`

Pre-flight:
  1. `git status --porcelain {beats.json} {book-log.json}` MUST be empty.
  2. Snapshot `{beats.json}.pre-migration` is written before any mutation.
  Both must succeed or the script exits non-zero without touching anything.

Usage:
  python scripts/migrate_beats_dedup_fields.py data/paris/beats.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# Top-level project import — keeps the canonical hash function in one place.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.api.models.nodes import _normalized_script_body_hash  # noqa: E402

LEGACY_UNKNOWN = "legacy_unknown"
LEGACY_AMBIGUOUS = "legacy_ambiguous"
DEFAULT_CITY = "paris"

# All ingested books to date — keys for legacy beat_id suffix matching.
# Add new books here as they're ingested. Order matters when slugs overlap;
# longest first (none today).
KNOWN_BOOK_SLUGS = ["around_and_about_paris"]


def parse_unified_v1_book_slug(
    beat_id: str, city_name: str, lens: str, topic_slug: str
) -> str:
    """unified_v1 beat_id = `{city}_{poi_slug}_{lens}_{book_slug}_{topic_slug}`.

    Strategy: strip city prefix and topic suffix, then split on the lens
    separator. Returns the parsed book_slug, or LEGACY_UNKNOWN on failure.
    """
    if not (city_name and lens and topic_slug):
        return LEGACY_UNKNOWN
    prefix = f"{city_name}_"
    suffix = f"_{topic_slug}"
    if not beat_id.startswith(prefix):
        return LEGACY_UNKNOWN
    middle = beat_id[len(prefix):]
    if not middle.endswith(suffix):
        return LEGACY_UNKNOWN
    middle = middle[: -len(suffix)]
    sep = f"_{lens}_"
    if sep not in middle:
        return LEGACY_UNKNOWN
    parts = middle.rsplit(sep, 1)
    if len(parts) != 2 or not parts[1]:
        return LEGACY_UNKNOWN
    return parts[1]


def parse_legacy_beat_id(beat_id: str, lens: str) -> tuple[str, str]:
    """legacy beat_id = `{poi_slug}_{lens}_{topic_slug}_{book_slug}`.

    Returns (book_slug, topic_slug). Either may be LEGACY_UNKNOWN if the
    beat_id doesn't conform.
    """
    if not lens:
        return LEGACY_UNKNOWN, LEGACY_UNKNOWN
    sep = f"_{lens}_"
    if sep not in beat_id:
        return LEGACY_UNKNOWN, LEGACY_UNKNOWN
    parts = beat_id.split(sep, 1)
    if len(parts) != 2 or not parts[1]:
        return LEGACY_UNKNOWN, LEGACY_UNKNOWN
    rest = parts[1]
    for book in KNOWN_BOOK_SLUGS:
        suffix = f"_{book}"
        if rest.endswith(suffix):
            topic = rest[: -len(suffix)]
            if topic:
                return book, topic
    # No known book_slug suffix — leave both unknown
    return LEGACY_UNKNOWN, LEGACY_UNKNOWN


def build_poi_chunk_index(book_log: dict) -> dict[str, set[str]]:
    """Map poi_name → set of distinct chunk slugs that touched it."""
    index: dict[str, set[str]] = defaultdict(set)
    for book in book_log.get("books_processed", []):
        for ch in book.get("chunks_processed", []):
            chunk_slug = ch.get("chunk", "")
            if not chunk_slug:
                continue
            for poi in ch.get("pois_touched", []):
                index[poi].add(chunk_slug)
    return index


def derive_source_chunk_slug(
    poi_name: str, poi_chunk_index: dict[str, set[str]]
) -> str:
    chunks = poi_chunk_index.get(poi_name, set())
    if len(chunks) == 1:
        return next(iter(chunks))
    if len(chunks) >= 2:
        return LEGACY_AMBIGUOUS
    return LEGACY_UNKNOWN


def is_real_value(v: str) -> bool:
    """A field is 'really set' (idempotency anchor) when it's non-empty AND
    not one of our sentinels. Sentinels are re-derivable on a later pass
    if upstream data improves."""
    return bool(v) and v not in (LEGACY_UNKNOWN, LEGACY_AMBIGUOUS)


def migrate_beat(beat: dict, poi_chunk_index: dict[str, set[str]]) -> dict:
    """Return a new beat dict with dedup fields populated. Idempotent:
    real values are preserved; only empty / sentinel values get filled."""
    out = dict(beat)

    # script_body_hash — always derivable, recompute when missing.
    if not out.get("script_body_hash"):
        out["script_body_hash"] = _normalized_script_body_hash(out.get("script_body", ""))

    # city_name — preserve real value, default to paris (only city live today).
    if not is_real_value(out.get("city_name", "")):
        out["city_name"] = DEFAULT_CITY

    prompt_version = (out.get("_meta") or {}).get("prompt_version", "")
    beat_id = out.get("beat_id", "")
    lens = out.get("lens", "")

    if prompt_version == "unified_v1":
        # topic_slug already lives on these beats; preserve it.
        if not out.get("topic_slug"):
            out["topic_slug"] = LEGACY_UNKNOWN
        # book_slug — parse from envelope.
        if not is_real_value(out.get("book_slug", "")):
            out["book_slug"] = parse_unified_v1_book_slug(
                beat_id, out.get("city_name", DEFAULT_CITY), lens, out["topic_slug"]
            )
    else:
        legacy_book, legacy_topic = parse_legacy_beat_id(beat_id, lens)
        if not is_real_value(out.get("book_slug", "")):
            out["book_slug"] = legacy_book
        if not is_real_value(out.get("topic_slug", "")):
            out["topic_slug"] = legacy_topic

    if not is_real_value(out.get("source_chunk_slug", "")):
        out["source_chunk_slug"] = derive_source_chunk_slug(
            out.get("poi_name", ""), poi_chunk_index
        )

    return out


# ── Pre-flight ──


def assert_git_clean(*paths: Path) -> None:
    """`git status --porcelain` must be empty for all named paths.

    Runs git from the beats file's parent directory so absolute paths from
    different working trees resolve against the right repository.
    """
    if not paths:
        return
    cwd = paths[0].resolve().parent
    rel_paths = [str(p.resolve()) for p in paths]
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", *rel_paths],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
    )
    if result.stdout.strip():
        raise SystemExit(
            "Migration aborted: git working tree is dirty for one of the "
            "target files. Commit or stash first.\n"
            f"git status --porcelain output:\n{result.stdout}"
        )


def write_snapshot(beats_path: Path) -> Path:
    """Write the `{beats_path}.pre-migration` snapshot before mutating."""
    snapshot = beats_path.with_suffix(beats_path.suffix + ".pre-migration")
    shutil.copy2(beats_path, snapshot)
    return snapshot


# ── Main ──


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("beats_path", type=Path)
    parser.add_argument(
        "--book-log",
        type=Path,
        default=None,
        help="Optional override; defaults to book-log.json next to beats.json",
    )
    args = parser.parse_args(argv)

    beats_path: Path = args.beats_path
    log_path: Path = args.book_log or (beats_path.parent / "book-log.json")

    if not beats_path.exists():
        print(f"migrate: beats file not found: {beats_path}", file=sys.stderr)
        return 2
    if not log_path.exists():
        print(f"migrate: book-log not found: {log_path}", file=sys.stderr)
        return 2

    try:
        assert_git_clean(beats_path, log_path)
    except subprocess.CalledProcessError as exc:
        print(f"migrate: git status check failed: {exc}", file=sys.stderr)
        return 2

    snapshot = write_snapshot(beats_path)
    print(f"migrate: wrote pre-mutation snapshot → {snapshot}")

    beats = json.loads(beats_path.read_text(encoding="utf-8"))
    book_log = json.loads(log_path.read_text(encoding="utf-8"))
    poi_chunk_index = build_poi_chunk_index(book_log)

    new_beats = [migrate_beat(b, poi_chunk_index) for b in beats]

    # Track changes for the operator report.
    changes = sum(1 for old, new in zip(beats, new_beats) if old != new)

    beats_path.write_text(
        json.dumps(new_beats, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Operator report
    parseable = sum(1 for b in new_beats if b["topic_slug"] != LEGACY_UNKNOWN)
    ambiguous = sum(1 for b in new_beats if b["source_chunk_slug"] == LEGACY_AMBIGUOUS)
    unknown_chunks = sum(1 for b in new_beats if b["source_chunk_slug"] == LEGACY_UNKNOWN)
    unified = sum(1 for b in new_beats if (b.get("_meta") or {}).get("prompt_version") == "unified_v1")
    unified_book_ok = sum(
        1
        for b in new_beats
        if (b.get("_meta") or {}).get("prompt_version") == "unified_v1"
        and b["book_slug"] == "around_and_about_paris"
    )
    print(
        f"migrate: {len(new_beats)} beats; {changes} mutated; "
        f"topic parseability {parseable}/{len(new_beats)}; "
        f"source_chunk_slug ambiguous={ambiguous} unknown={unknown_chunks}; "
        f"unified_v1 book_slug correct: {unified_book_ok}/{unified}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
