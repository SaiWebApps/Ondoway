#!/usr/bin/env python
"""Rebuild a city's book log from the beats it is supposed to describe.

Two incompatible shapes are on disk. Paris carries the documented one — a
`books_processed` list of `{book_title, author, chunks_processed: [...]}`. New York
carries `{book-slug: {chunk-key: {...}}}` at the top level, with abbreviated chunk keys
(`chunk-07`, where the beats say `chunk-07-upper-east-side`); its Wikipedia work in the
documented shape; and one book whose `chunks_processed` is the integer 12.

The cost of that split is not tidiness. `/unified-beat-extract`'s PRE-CHECK reads
`books_processed` to refuse a chunk that was already processed, so **New York books have
no duplicate-extraction protection at all**, and `/beat-wipe` cannot remove a log entry
it cannot find. Both bite the next book ingested, not only a re-extraction.

**Rebuilt from the beats, not translated from the old shape.** Every beat carries
`book_slug`, the full `source_chunk_slug`, `poi_name` and a `source_attribution` naming
the book and author, so the structure and most fields derive from the corpus the log
describes. That is what makes the abbreviated keys recoverable — the beats know the full
slug — and it means drift between log and corpus is corrected rather than faithfully
carried across.

Three fields exist only in the log and cannot be derived: `processed_at`,
`pois_created`, `pois_mentioned_no_content`. Those are carried over from whichever shape
the old log used, matched per book and chunk. A chunk whose old entry cannot be found
keeps everything derivable and says so in the report rather than inventing a date.

Dry-run by default, like `wipe_beats`. Both logs are tracked in git.

Run as:
    uv run python scripts/rebuild_book_log.py --city new_york
    uv run python scripts/rebuild_book_log.py --city new_york --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.wipe_beats import LEGACY_AMBIGUOUS, same_book

#: Fields a beat cannot prove. Carried across from the old log when its entry is found.
CARRIED_FIELDS = ("processed_at", "pois_created", "pois_mentioned_no_content", "new_pois_flagged")

_REPO_ROOT = Path(__file__).resolve().parent.parent


def log_path(city: str) -> Path:
    return _REPO_ROOT / "data" / city / "book-log.json"


def beats_path(city: str) -> Path:
    return _REPO_ROOT / "data" / city / "beats.json"


def derive_from_beats(beats: list[dict]) -> dict[str, dict[str, Any]]:
    """What the corpus proves about which book gave which chunk which beats.

    Keyed by the `book_slug` the beats actually carry, in the convention they carry it
    — the log is rebuilt to describe the corpus, not to impose a spelling on it.
    """
    books: dict[str, dict[str, Any]] = {}
    for beat in beats:
        slug = beat.get("book_slug") or ""
        chunk = beat.get("source_chunk_slug") or ""
        if not slug or not chunk or chunk == LEGACY_AMBIGUOUS:
            continue
        attribution = beat.get("source_attribution") or {}
        book = books.setdefault(
            slug,
            {
                "book_title": attribution.get("book_title") or slug,
                "author": attribution.get("author") or "",
                "chunks": defaultdict(lambda: {"beats_extracted": 0, "pois_touched": set()}),
            },
        )
        if not book["author"] and attribution.get("author"):
            book["author"] = attribution["author"]
        entry = book["chunks"][chunk]
        entry["beats_extracted"] += 1
        if beat.get("poi_name"):
            entry["pois_touched"].add(beat["poi_name"])
    return books


def existing_entries(log: dict) -> list[tuple[str, str, dict]]:
    """`(book_key, chunk_key, payload)` from either shape, so neither is privileged."""
    out: list[tuple[str, str, dict]] = []
    for book in log.get("books_processed", []) or []:
        if not isinstance(book, dict):
            continue
        key = book.get("book_title") or ""
        chunks = book.get("chunks_processed")
        # A third variant: one book records `chunks_processed: 12`, a count where the
        # others hold a list. It names no chunk, so it carries nothing — the beats say
        # which chunks that book produced.
        if not isinstance(chunks, list):
            continue
        for chunk in chunks:
            if isinstance(chunk, dict):
                out.append((key, chunk.get("chunk", ""), chunk))
            elif isinstance(chunk, str):
                out.append((key, chunk, {}))
    for key, value in log.items():
        if key in ("books_processed", "city", "city_name") or not isinstance(value, dict):
            continue
        for chunk_key, payload in value.items():
            if isinstance(payload, dict):
                out.append((key, chunk_key, payload))
    return out


def match_old_entry(
    book_slug: str,
    book_title: str,
    chunk_slug: str,
    existing: list[tuple[str, str, dict]],
) -> tuple[dict | None, str]:
    """The old entry for this chunk, and how it was found.

    A book is identified by its slug OR its title, because the two shapes key on
    different things and neither derives from the other. The Paris log keys on
    "Frommer's 24 Great Walks in Paris" where its beats carry
    `frommers-24-great-walks`: no canonicalisation of punctuation bridges a dropped
    "in Paris" and an apostrophe. The beats carry both, so both are compared.

    Exact chunk first, then a prefix, because New York abbreviates
    `chunk-07-upper-east-side` to `chunk-07`. A prefix matching more than one chunk of
    the same book carries nothing and is reported — a guess there would stamp one
    chunk's date onto another's beats.
    """
    same = [
        (c, p)
        for b, c, p in existing
        if same_book(b, book_slug) or (book_title and same_book(b, book_title))
    ]
    for chunk_key, payload in same:
        if chunk_key == chunk_slug:
            return payload, "exact"
    prefixed = [(c, p) for c, p in same if c and chunk_slug.startswith(c)]
    if len(prefixed) == 1:
        return prefixed[0][1], f"prefix ({prefixed[0][0]})"
    if len(prefixed) > 1:
        return None, f"ambiguous ({', '.join(c for c, _ in prefixed)})"
    return None, "not found"


def rebuild(city: str, beats: list[dict], log: dict) -> tuple[dict, list[str]]:
    """The log the corpus describes, and a line per chunk saying where each came from."""
    derived = derive_from_beats(beats)
    existing = existing_entries(log)
    notes: list[str] = []
    books_processed = []
    for slug in sorted(derived):
        book = derived[slug]
        chunks = []
        for chunk_slug in sorted(book["chunks"]):
            counted = book["chunks"][chunk_slug]
            entry: dict[str, Any] = {
                "chunk": chunk_slug,
                "beats_extracted": counted["beats_extracted"],
                "pois_touched": sorted(counted["pois_touched"]),
            }
            old, how = match_old_entry(slug, book["book_title"], chunk_slug, existing)
            carried = []
            if old:
                for field in CARRIED_FIELDS:
                    if field in old:
                        entry[field] = old[field]
                        carried.append(field)
            notes.append(
                f"  {slug}/{chunk_slug}: {counted['beats_extracted']} beats, "
                f"{len(counted['pois_touched'])} POIs — old entry {how}"
                + (f", carried {', '.join(carried)}" if carried else "")
            )
            chunks.append(entry)
        books_processed.append(
            {
                "book_title": book["book_title"],
                "author": book["author"],
                "book_slug": slug,
                "chunks_processed": chunks,
            }
        )
    return {"city": city, "books_processed": books_processed}, notes


def check(rebuilt: dict, beats: list[dict]) -> list[str]:
    """Every chunk with beats is in the log, and every log chunk has beats.

    The point of rebuilding from the corpus rather than translating a shape: the result
    is checkable against the thing it describes.
    """
    logged = {
        (b["book_slug"], c["chunk"])
        for b in rebuilt["books_processed"]
        for c in b["chunks_processed"]
    }
    from_beats = {
        (b.get("book_slug") or "", b.get("source_chunk_slug") or "")
        for b in beats
        if (b.get("source_chunk_slug") or "") not in ("", LEGACY_AMBIGUOUS) and b.get("book_slug")
    }
    problems = []
    for pair in sorted(from_beats - logged):
        problems.append(f"beats exist but no log entry: {pair[0]}/{pair[1]}")
    for pair in sorted(logged - from_beats):
        problems.append(f"log entry with no beats: {pair[0]}/{pair[1]}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", required=True, help="City slug under data/.")
    parser.add_argument("--apply", action="store_true", help="Write. Without it, dry-run.")
    parser.add_argument("--quiet", action="store_true", help="Skip the per-chunk lines.")
    args = parser.parse_args(argv)

    bp, lp = beats_path(args.city), log_path(args.city)
    if not bp.is_file():
        print(f"error: {bp} does not exist", file=sys.stderr)
        return 2
    beats = json.loads(bp.read_text(encoding="utf-8"))
    log = json.loads(lp.read_text(encoding="utf-8")) if lp.is_file() else {}

    rebuilt, notes = rebuild(args.city, beats, log)
    problems = check(rebuilt, beats)

    if not args.quiet:
        for line in notes:
            print(line)
    books = rebuilt["books_processed"]
    chunks = sum(len(b["chunks_processed"]) for b in books)
    orphaned = sum(1 for b in beats if (b.get("source_chunk_slug") or "") in ("", LEGACY_AMBIGUOUS))
    print(
        f"\n{args.city}: {len(books)} books, {chunks} chunks, {len(beats)} beats\n"
        f"  {sum(1 for n in notes if 'exact' in n)} matched an old entry exactly, "
        f"{sum(1 for n in notes if 'prefix' in n)} by prefix, "
        f"{sum(1 for n in notes if 'not found' in n)} had none, "
        f"{sum(1 for n in notes if 'ambiguous' in n)} ambiguous\n"
        f"  {orphaned} beats name no chunk and are not described by any log entry"
    )
    if problems:
        print("\nthe rebuilt log disagrees with the beats:")
        for line in problems[:10]:
            print(f"  {line}")
        return 1

    if not args.apply:
        print("\ndry-run: nothing written. Re-run with --apply to persist.")
        return 0
    lp.write_text(json.dumps(rebuilt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwritten: {lp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
