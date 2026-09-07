#!/usr/bin/env python
"""Remove beats and the matching book-log entry for a single chunk.

Usage:
  python scripts/wipe_beats.py {city}/{book-slug} --chunk {chunk-slug} [--apply]
                               [--beats-path path/to/beats.json]
                               [--log-path path/to/book-log.json]

Example (dry run):
  python scripts/wipe_beats.py paris/around-and-about-paris \
         --chunk chunk-15-5th-arr-val-de-grace

Example (apply):
  python scripts/wipe_beats.py paris/around-and-about-paris \
         --chunk chunk-15-5th-arr-val-de-grace --apply

Semantics:
- Selects beats where `book_slug == {book_slug}` AND
  `source_chunk_slug == {chunk_slug}` AND
  `source_chunk_slug != "legacy_ambiguous"` (BP-8 — legacy_ambiguous
  beats are never deleted by /beat-wipe).
- Removes the matching entry from the book's `chunks_processed` list
  in `book-log.json`.
- Dry-run by default: prints the plan, touches nothing on disk. Pass
  `--apply` to persist.
- Idempotent: re-running after a wipe prints `already clean: no
  matching beats or log entry` and leaves both files byte-identical
  (no commit is issued when there is nothing to change).
- Writes go through `scripts.beats_io.commit` — validator-gated and
  atomic.

CLI normalization:
- `book_slug` positional accepts `{city}/{book-slug}` or just
  `{book-slug}`, in either convention. Beats carry two — `around_and_about_paris`
  from the title-derived migration and `lonely-planet-new-york-city` from the
  extractor — and both sides are compared on a canonical form, so a slug typed
  either way finds its beats.
- The city prefix, when present, drives the default data paths.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Allow `python scripts/wipe_beats.py ...` as well as `python -m scripts.wipe_beats`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.beats_io import BeatValidationError, commit

LEGACY_AMBIGUOUS = "legacy_ambiguous"


def normalize_book_slug(arg: str) -> str:
    """Strip a leading `{city}/`, leaving the book slug as the caller wrote it."""
    return arg.split("/", 1)[1] if "/" in arg else arg


def same_book(a: str, b: str) -> bool:
    """Whether two book slugs name the same book.

    Two conventions are in the corpus: `around_and_about_paris` from the migration
    that derived slugs from titles, and `lonely-planet-new-york-city` from the
    extractor that wrote them from directory names. Every New York beat uses the
    dashed form and 457 Paris beats do. Comparing on one convention silently matched
    nothing for those and printed "already clean" — a no-op that reads as success and
    leaves the caller believing a chunk was wiped.
    """
    return _canonical(a) == _canonical(b)


def _canonical(slug: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (slug or "").lower()).strip("_")


def derive_city(arg: str) -> str | None:
    return arg.split("/", 1)[0] if "/" in arg else None


def slugify_title(title: str) -> str:
    """Match the migration's title → book_slug derivation."""
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")


def find_matching_beats(
    beats: list[dict], book_slug: str, chunk_slug: str
) -> tuple[list[dict], list[dict]]:
    """Split beats into (kept, removed)."""
    kept: list[dict] = []
    removed: list[dict] = []
    for beat in beats:
        source_chunk = beat.get("source_chunk_slug", "")
        if (
            same_book(beat.get("book_slug", ""), book_slug)
            and source_chunk == chunk_slug
            and source_chunk != LEGACY_AMBIGUOUS
        ):
            removed.append(beat)
        else:
            kept.append(beat)
    return kept, removed


def remove_chunk_from_log(log: dict, book_slug: str, chunk_slug: str) -> tuple[dict, dict | None]:
    """Return (updated_log, removed_entry_or_None). Non-destructive on input."""
    # Deep-ish copy via json round-trip — safe for this small dict
    # and avoids accidentally mutating the caller's copy.
    updated: dict[str, Any] = json.loads(json.dumps(log))
    removed_entry: dict | None = None
    for book in updated.get("books_processed", []):
        if not same_book(slugify_title(book.get("book_title", "")), book_slug):
            continue
        chunks = book.get("chunks_processed", [])
        for i, entry in enumerate(chunks):
            chunk_id = entry.get("chunk") if isinstance(entry, dict) else entry
            if chunk_id == chunk_slug:
                removed_entry = chunks.pop(i)
                return updated, removed_entry
    return updated, removed_entry


def _default_beats_path(city: str | None) -> Path | None:
    return Path(f"data/{city}/beats.json") if city else None


def _default_log_path(city: str | None) -> Path | None:
    return Path(f"data/{city}/book-log.json") if city else None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="wipe_beats.py",
        description="Remove beats and the matching book-log entry for a single chunk.",
    )
    parser.add_argument(
        "book_slug",
        help="{city}/{book-slug} (e.g. paris/around-and-about-paris) or just {book-slug}",
    )
    parser.add_argument(
        "--chunk", required=True, help="chunk slug (e.g. chunk-15-5th-arr-val-de-grace)"
    )
    parser.add_argument("--beats-path", help="override default data/{city}/beats.json")
    parser.add_argument("--log-path", help="override default data/{city}/book-log.json")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="persist changes. Without this flag, runs as dry-run.",
    )
    args = parser.parse_args(argv)

    book_slug = normalize_book_slug(args.book_slug)
    chunk_slug = args.chunk
    city = derive_city(args.book_slug)

    # BP-8 — the sentinel is never a valid wipe target. The beat-side
    # filter already refuses to remove beats carrying this source_chunk,
    # but we also refuse to touch any book-log entry that happens to
    # carry the literal string. This makes the guard symmetric on both
    # sides of the wipe (beats AND log) regardless of what the user
    # types on the command line.
    if chunk_slug == LEGACY_AMBIGUOUS:
        print(
            f"refused: '{LEGACY_AMBIGUOUS}' is a sentinel, not a chunk. "
            f"Wipe does not operate on legacy_ambiguous beats — delete them "
            f"manually if you must."
        )
        return 0

    beats_path = Path(args.beats_path) if args.beats_path else _default_beats_path(city)
    log_path = Path(args.log_path) if args.log_path else _default_log_path(city)

    if beats_path is None or log_path is None:
        print(
            "error: book_slug must include city (e.g. paris/around-and-about-paris) "
            "or --beats-path and --log-path must be given",
            file=sys.stderr,
        )
        return 2

    if not beats_path.exists():
        print(f"error: {beats_path} does not exist", file=sys.stderr)
        return 2
    if not log_path.exists():
        print(f"error: {log_path} does not exist", file=sys.stderr)
        return 2

    beats = json.loads(beats_path.read_text(encoding="utf-8"))
    log = json.loads(log_path.read_text(encoding="utf-8"))

    kept, removed_beats = find_matching_beats(beats, book_slug, chunk_slug)
    updated_log, removed_log_entry = remove_chunk_from_log(log, book_slug, chunk_slug)

    # BP-8 audit — sanity-check the selection filter really skipped
    # legacy_ambiguous beats for this book_slug + chunk pair.
    ambiguous_survivors = [
        b
        for b in kept
        if same_book(b.get("book_slug", ""), book_slug)
        and b.get("source_chunk_slug") == LEGACY_AMBIGUOUS
    ]

    if not removed_beats and removed_log_entry is None:
        print(
            f"already clean: no matching beats or log entry for "
            f"book_slug={book_slug} chunk={chunk_slug}"
        )
        return 0

    print(
        f"plan: remove {len(removed_beats)} beat(s) and "
        f"{'1' if removed_log_entry else '0'} book-log chunk entry "
        f"for book_slug={book_slug} chunk={chunk_slug}"
    )
    for beat in removed_beats:
        print(f"  - {beat.get('beat_id', '<no-beat-id>')}")
    if removed_log_entry is not None:
        print(f"  - log: {removed_log_entry.get('chunk', chunk_slug)}")
    if ambiguous_survivors:
        print(
            f"  (kept {len(ambiguous_survivors)} legacy_ambiguous beat(s) "
            f"at the same book_slug — wipe does not touch those)"
        )

    if not args.apply:
        print("dry-run: no files written. Re-run with --apply to persist.")
        return 0

    try:
        commit(
            kept,
            updated_log,
            beats_path=beats_path,
            log_path=log_path,
        )
    except BeatValidationError as exc:
        print(f"wipe aborted — validator rejected the result:\n{exc}", file=sys.stderr)
        return 1

    print(f"wiped: {len(removed_beats)} beat(s); both files updated atomically.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
