#!/usr/bin/env python
"""Beat constructor helper for `/unified-beat-extract`.

Eliminates the per-extraction boilerplate of building a Beat dict by hand:
hashing the body, computing duration_sec, slugging beat_id components,
filling _meta and source_attribution defaults, defaulting fact_check.

Callers create a `BookContext` once for the run, then call `make_beat(...)`
once per beat with only the content-level fields. All mechanical fields
are derived.

This is a pure-construction helper — it does NOT validate. Validation is
`scripts/extract_validators.py`. Both run before `scripts.beats_io.commit`.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class BookContext:
    """Per-extraction-run defaults applied to every beat."""

    book_title: str
    author: str
    book_slug: str
    chunk_slug: str
    chapter: str
    page: str
    city_name: str = "paris"
    prompt_version: str = "unified_v2"
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(s: str) -> str:
    """POI/topic/lens slug normalization. Lowercase, snake_case, strip
    punctuation, fold accented characters to ASCII (`Étoile`→`etoile`).
    Stable for the same input across runs."""
    # NFKD splits 'é' into 'e' + combining-acute, then we drop the combining marks.
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )
    return _SLUG_RE.sub("_", folded.lower()).strip("_")


def hash_body(body: str) -> str:
    """SHA-256 of whitespace-collapsed lowercased body. The same hash
    `validate_beats.py` checks for collisions."""
    norm = re.sub(r"\s+", " ", body.lower().strip())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def word_count(s: str) -> int:
    return len(re.findall(r"\S+", s))


def duration_sec(body: str) -> int:
    """Computed duration at 2.5 words/sec (150 wpm). Always derived from the
    body, never set by the model."""
    return round(word_count(body) / 2.5)


def beat_id(*, ctx: BookContext, poi_name: str, lens: str, topic_slug: str) -> str:
    """The beat id: `{city}_{poi}_{lens}_{book}_{topic}`."""
    return f"{ctx.city_name}_{slugify(poi_name)}_{lens}_{ctx.book_slug}_{topic_slug}"


def make_beat(
    *,
    ctx: BookContext,
    poi_name: str,
    lens: str,
    topic_slug: str,
    script_body: str,
    source_passage: str,
    beat_length_class: str,
    # narrative classification
    beat_type: str,
    narrative_function: str,
    emotional_register: str,
    subject_tag: str,
    entities: list[str],
    sensory_anchor: bool,
    # optional content
    sub_location: str | None = None,
    trigger_address: str | None = None,
    parent_poi: str | None = None,
    physical_cues: list[dict] | None = None,
    inline_foreign_phrases: list[dict] | None = None,
    pronunciation: str | None = None,
    key_claims: list[str] | None = None,
    kid_friendly: str = "yes",
    new_poi: bool = False,
    # honesty fields: what the extractor admits it did not take from the source
    extractor_state: str = "clean",
    flagged_claims: list[str] | None = None,
    fact_check_notes: str = "",
) -> dict[str, Any]:
    """Construct a single Beat dict, auto-filling all mechanical fields.

    The required keyword arguments are the fields every beat must carry. The
    caller decides content-level meaning; this function fills the rest.
    """
    body = script_body.strip()
    topic = slugify(topic_slug)

    return {
        "beat_id": beat_id(ctx=ctx, poi_name=poi_name, lens=lens, topic_slug=topic),
        "city_name": ctx.city_name,
        "poi_name": poi_name,
        "parent_poi": parent_poi,
        "lens": lens,
        "topic_slug": topic,
        "book_slug": ctx.book_slug,
        "source_chunk_slug": ctx.chunk_slug,
        "sub_location": sub_location,
        "trigger_address": trigger_address,
        "script_body": body,
        "script_body_hash": hash_body(body),
        "beat_length_class": beat_length_class,
        "duration_sec": duration_sec(body),
        "kid_friendly": kid_friendly,
        "entities": list(entities),
        "sensory_anchor": sensory_anchor,
        "narrative_function": narrative_function,
        "beat_type": beat_type,
        "emotional_register": emotional_register,
        "subject_tag": subject_tag,
        "physical_cues": list(physical_cues or []),
        "inline_foreign_phrases": list(inline_foreign_phrases or []),
        "pronunciation": pronunciation,
        "key_claims": list(key_claims or []),
        "source_passage": source_passage.strip(),
        "source_attribution": {
            "book_title": ctx.book_title,
            "author": ctx.author,
            "chapter": ctx.chapter,
            "page": ctx.page,
        },
        "fact_check": {
            "flagged_claims": list(flagged_claims or []),
            "status": "unverified",
            "extractor_state": extractor_state,
            "notes": fact_check_notes,
        },
        "new_poi": new_poi,
        "_meta": {
            "prompt_version": ctx.prompt_version,
            "generated_at": ctx.generated_at,
            "city_name": ctx.city_name,
        },
    }
