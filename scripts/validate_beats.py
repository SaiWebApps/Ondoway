#!/usr/bin/env python
"""Validate a beats.json file for duplicate beats and ungrounded Wikipedia beats.

Shape dispatch (decisions.shape_detection): `validate()` first classifies the
file's shape from its records, then routes to one of two independent
validators:

- All records "legacy"-shaped (neither `claims` nor `narration` present, and
  carrying more than a bare `beat_id`) — the seven checks below, unchanged.
  An empty beats list is vacuously all-legacy and passes.
- All records "new"-shaped (both `claims` and `narration` present) —
  `src.ingest.model.validate`, given `chunks_root` (explicit `--chunks-root`,
  else derived the same way the legacy grounding gate below derives
  `Books/{City}`). An undecidable root on this path is a hard error, never a
  soft-skip.
- Anything else is refused before either validator runs: a mix of new- and
  legacy-shaped records is `SHAPE_MIXED`; a record that is neither (missing
  virtually everything, or carrying exactly one of `claims`/`narration`, or
  not a dict) is `SHAPE_UNKNOWN`. Per-record shape is structural — it never
  keys on `script_body` — matching `src.ingest.model`'s own per-record rule
  except for the "just a beat_id, nothing else" case, which that rule alone
  can't tell apart from a genuine legacy beat.

Checks collection-level invariants the Pydantic model can't enforce:

1. `script_body_hash` is unique across all beats in the file.
2. The identity tuple (city_name, poi_name, lens, book_slug, topic_slug) is
   unique across all beats. `legacy_unknown` in the `book_slug` or `topic_slug`
   position acts as a wildcard: two rows that both carry `legacy_unknown` in
   the same position do NOT collide with each other (the legacy migration
   uses this sentinel where the slug couldn't be parsed; the wildcard avoids
   forcing fake collisions on un-recoverable history).
3. Every `book_slug == "wikipedia"` beat's `source_passage` is grounded in its
   pinned revision file (`{beats_dir}/wikipedia/{source_chunk_slug}.txt`). This
   makes the source-grounding gate a hard commit-time chokepoint rather than an
   extractor honor-system check — a beat reconstructed from memory cannot be
   committed even if the extractor skipped its own validation.
4. Every BOOK beat's `source_passage` is grounded in its source chunk
   (`Books/{City}/{book-slug}/{source_chunk_slug}.txt`), with three soft-skips
   so the gate never breaks the historical corpus: legacy-sentinel chunks,
   chunks not locatable on disk, and beats whose `script_body_hash` is listed in
   `{beats_dir}/grounding_grandfathered.json` (legacy terse-note beats predating
   the verbatim convention — a shrinking cleanup backlog). New extractions and
   any edit to a grandfathered beat are hard-enforced.
5. A `verified` beat still carries the body it was verified against:
   `fact_check.verified_body_hash` (stamped by /fact-check) must equal the
   current `script_body_hash`. A mutation that rewrites a verified beat's body
   without re-verifying (e.g. a dedup merge) is blocked, so a "verified" badge
   can never sit on unverified text.
6. `fact_check.status`, when present, is one of {verified, corrected, disputed,
   unverified}. A controlled vocabulary keeps the field a reliable gate (the
   upload's disputed-exclusion can't be defeated by a "dispute" typo).

Scoped to the beats file's own city directory + the repo's `Books/{City}/`
chunk sources; never reads any other city or global state. The pre-upload gate
(AC-9), end-of-extraction gate (AC-11), and `beats_io.commit` all call this
script with the city's beats path.

Exit codes:
  0 — all checks pass
  1 — at least one collision, or a refused SHAPE_MIXED/SHAPE_UNKNOWN file
      (printed with full beat IDs and the conflict type)
  2 — file unreadable, not a JSON list, or (new-shape only) no chunks root
      could be found or given (operator error, distinct from a real
      data-integrity failure)

Usage:
  python scripts/validate_beats.py data/paris/beats.json
  python scripts/validate_beats.py data/new_york/beats.json --chunks-root Books/new_york
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.extract_validators import source_grounding_gate
from src.ingest import model as ingest_model

LEGACY_WILDCARD = "legacy_unknown"
WIKIPEDIA_BOOK_SLUG = "wikipedia"


def _load_beats(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"{path} is not a JSON list of beats")
    return data


def _check_hash_uniqueness(beats: list[dict]) -> list[str]:
    by_hash: dict[str, list[str]] = defaultdict(list)
    for beat in beats:
        h = beat.get("script_body_hash", "")
        if not h:
            continue  # missing hash is a different problem; flagged below
        by_hash[h].append(beat.get("beat_id", "<no-beat-id>"))
    errors: list[str] = []
    for h, ids in by_hash.items():
        if len(ids) >= 2:
            errors.append(
                f"HASH_COLLISION script_body_hash={h} beats={ids}"
            )
    missing = [b.get("beat_id", "<no-beat-id>") for b in beats if not b.get("script_body_hash")]
    if missing:
        errors.append(f"HASH_MISSING beats lack script_body_hash: {missing}")
    return errors


def _check_beat_id_uniqueness(beats: list[dict]) -> list[str]:
    """Every `beat_id` must be unique across the file.

    The upload path MERGEs ``NarrativeBeat {beat_id: ...}`` with NO DB uniqueness
    constraint behind it, so two beats sharing a beat_id collapse onto a SINGLE
    graph node — the second silently SET-overwriting the first, losing one beat.
    (Empty-slug non-Latin POI names are a real source of identical ids.) This is
    the collection-level uniqueness the Pydantic model can't see; blocked here at
    commit so it can never reach the graph. Empty/missing beat_ids are a
    different defect (the uploader refuses them via its ``no_beat_id`` skip), so
    they are not counted as collisions here.
    """
    by_id: dict[str, int] = defaultdict(int)
    for beat in beats:
        beat_id = beat.get("beat_id", "")
        if not beat_id:
            continue
        by_id[beat_id] += 1
    return [
        f"BEAT_ID_COLLISION beat_id={beat_id!r} appears {count} times — beats would "
        f"collapse onto one NarrativeBeat node"
        for beat_id, count in by_id.items()
        if count >= 2
    ]


def _identity_key(beat: dict) -> tuple[str, str, str, str, str] | None:
    """Build the identity tuple. Returns None when wildcards make this row
    non-collidable with any other row carrying the same wildcard in the same
    position. Two `legacy_unknown` book_slug rows do NOT collide; one
    `legacy_unknown` + one concrete `around_and_about_paris` for the same
    POI/lens/topic likewise do NOT collide (we can't know whether they're the
    same beat). The wildcard is permissive on purpose.
    """
    city_name = beat.get("city_name", "")
    poi_name = beat.get("poi_name", "")
    lens = beat.get("lens", "")
    book_slug = beat.get("book_slug", "")
    topic_slug = beat.get("topic_slug", "")
    if book_slug == LEGACY_WILDCARD or topic_slug == LEGACY_WILDCARD:
        return None
    return (city_name, poi_name, lens, book_slug, topic_slug)


def _check_identity_uniqueness(beats: list[dict]) -> list[str]:
    by_key: dict[tuple, list[str]] = defaultdict(list)
    for beat in beats:
        key = _identity_key(beat)
        if key is None:
            continue
        by_key[key].append(beat.get("beat_id", "<no-beat-id>"))
    errors: list[str] = []
    for key, ids in by_key.items():
        if len(ids) >= 2:
            errors.append(
                "IDENTITY_COLLISION "
                f"(city_name,poi_name,lens,book_slug,topic_slug)={key} beats={ids}"
            )
    return errors


def _check_wikipedia_grounding(beats: list[dict], wiki_dir: Path) -> list[str]:
    """Every `book_slug == "wikipedia"` beat must quote its pinned revision file.

    This is the commit-time enforcement of the source-grounding gate: each
    Wikipedia beat's `source_passage` must trace to `{wiki_dir}/{chunk}.txt`
    (the exact revision text saved by `make wiki-fetch`). A beat reconstructed
    from memory, or pointing at a deleted/renamed pinned source, fails here —
    so the protection no longer depends on an extractor choosing to self-check.
    Mirrors the threshold used by `extract_validators.validate_beat`.
    """
    errors: list[str] = []
    chunk_text_cache: dict[str, str | None] = {}
    for beat in beats:
        if beat.get("book_slug") != WIKIPEDIA_BOOK_SLUG:
            continue
        beat_id = beat.get("beat_id", "<no-beat-id>")
        chunk = beat.get("source_chunk_slug", "")
        if not chunk:
            errors.append(f"WIKIPEDIA_NO_CHUNK {beat_id} has no source_chunk_slug")
            continue
        if chunk not in chunk_text_cache:
            src = wiki_dir / f"{chunk}.txt"
            chunk_text_cache[chunk] = (
                src.read_text(encoding="utf-8") if src.exists() else None
            )
        chunk_text = chunk_text_cache[chunk]
        if chunk_text is None:
            errors.append(
                f"WIKIPEDIA_MISSING_SOURCE {beat_id}: pinned source {chunk}.txt "
                f"not found in {wiki_dir}"
            )
            continue
        total, ungrounded = source_grounding_gate(beat.get("source_passage", ""), chunk_text)
        if len(ungrounded) >= 2 and len(ungrounded) / total > 0.3:
            errors.append(
                f"WIKIPEDIA_UNGROUNDED {beat_id}: {len(ungrounded)}/{total} source_passage "
                f"sentence(s) absent from {chunk}.txt — not quoted from the pinned source"
            )
    return errors


_NON_BOOK_SLUGS = {WIKIPEDIA_BOOK_SLUG, "legacy_unknown", ""}


def _book_chunk_text(beats_path: Path, book_slug: str, chunk_slug: str) -> str | None:
    """Locate a book beat's source chunk: `Books/{City}/{book-dir}/{chunk}.txt`.

    Returns None when not locatable (legacy beats, unmapped books) so the gate
    soft-skips rather than breaking commits. Derives the Books root from the
    beats path (`data/{city}/beats.json` → repo-root → `Books/`), matching the
    city directory case-insensitively (data dir `paris` ↔ `Books/Paris`).
    """
    resolved = beats_path.resolve()
    books_root = resolved.parent.parent.parent / "Books"
    if not books_root.exists():
        return None
    city_slug = resolved.parent.name
    city_dir = next(
        (d for d in books_root.iterdir() if d.is_dir() and d.name.lower() == city_slug.lower()),
        None,
    )
    if city_dir is None:
        return None
    chunk_file = city_dir / book_slug.replace("_", "-") / f"{chunk_slug}.txt"
    return chunk_file.read_text(encoding="utf-8") if chunk_file.exists() else None


def _load_grounding_grandfather(beats_path: Path) -> set[str]:
    """`script_body_hash`es of legacy book beats exempt from grounding.

    These predate the verbatim-source_passage convention (older terse-note
    style; facts trace to the chunk but not as substrings). A shrinking cleanup
    backlog: re-quoting a beat verbatim changes its hash and drops it from the
    exemption automatically, at which point it must ground.
    """
    f = beats_path.parent / "grounding_grandfathered.json"
    if not f.exists():
        return set()
    data = json.loads(f.read_text(encoding="utf-8"))
    return set(data.get("exempt_script_body_hashes", []))


def _check_book_grounding(beats: list[dict], beats_path: Path) -> list[str]:
    """Hard-fail any BOOK beat whose `source_passage` isn't grounded in its
    pinned chunk file — except legacy beats grandfathered by hash, beats whose
    chunk isn't locatable (soft-skip), and Wikipedia (handled separately).
    Mirrors the Wikipedia gate's threshold; this is what makes book extraction
    a hard commit-time chokepoint instead of an extractor honor-system check.
    """
    exempt = _load_grounding_grandfather(beats_path)
    errors: list[str] = []
    text_cache: dict[tuple[str, str], str | None] = {}
    for beat in beats:
        book_slug = beat.get("book_slug", "")
        if book_slug in _NON_BOOK_SLUGS:
            continue
        chunk = beat.get("source_chunk_slug", "")
        if not chunk or "legacy" in chunk.lower():
            continue  # legacy sentinel chunk — not locatable, soft-skip
        if beat.get("script_body_hash", "") in exempt:
            continue  # grandfathered legacy terse-note beat (tracked cleanup debt)
        key = (book_slug, chunk)
        if key not in text_cache:
            text_cache[key] = _book_chunk_text(beats_path, book_slug, chunk)
        chunk_text = text_cache[key]
        if chunk_text is None:
            continue  # chunk file not locatable — soft-skip (don't break commits)
        total, ungrounded = source_grounding_gate(beat.get("source_passage", ""), chunk_text)
        if len(ungrounded) >= 2 and len(ungrounded) / total > 0.3:
            beat_id = beat.get("beat_id", "<no-beat-id>")
            errors.append(
                f"BOOK_UNGROUNDED {beat_id}: {len(ungrounded)}/{total} source_passage "
                f"sentence(s) absent from {chunk}.txt — not quoted from the source chunk"
            )
    return errors


def _check_verification_freshness(beats: list[dict]) -> list[str]:
    """A `verified` beat must still carry the body it was verified against.

    When `/fact-check` marks a beat `verified` it stamps
    `fact_check.verified_body_hash = script_body_hash`. If a later operation
    rewrites the body (e.g. a dedup COMBINE) but carries the old verification
    forward, the stamp no longer matches the current `script_body_hash` — a
    "verified" badge on text no human checked. That is blocked here.

    Enforced only when the stamp is present, so beats verified before this field
    existed (or not yet re-stamped) are not falsely failed — they are simply
    unprotected until re-verified.
    """
    errors: list[str] = []
    for beat in beats:
        fc = beat.get("fact_check") or {}
        if fc.get("status") != "verified":
            continue
        stamped = fc.get("verified_body_hash")
        if stamped and stamped != beat.get("script_body_hash"):
            errors.append(
                f"VERIFICATION_STALE {beat.get('beat_id', '<no-beat-id>')}: status=verified but "
                f"verified_body_hash != script_body_hash — body changed since verification; "
                f"re-run /fact-check or drop the verified status"
            )
    return errors


_VALID_STATUSES = {"verified", "corrected", "disputed", "unverified"}


def _check_status_vocabulary(beats: list[dict]) -> list[str]:
    """`fact_check.status`, when present, must be one of the four controlled
    values. Keeps the field a reliable gate: ad-hoc variants ("dispute" vs
    "disputed", "auto_corrected", "needs_review", …) silently defeat any
    status-based filter (e.g. the upload's disputed-exclusion), so they are
    blocked at commit. Absent/empty status is allowed (not yet fact-checked).
    """
    errors: list[str] = []
    for beat in beats:
        status = (beat.get("fact_check") or {}).get("status")
        if status in (None, ""):
            continue
        if status not in _VALID_STATUSES:
            errors.append(
                f"STATUS_INVALID {beat.get('beat_id', '<no-beat-id>')}: "
                f"fact_check.status={status!r} not in {sorted(_VALID_STATUSES)}"
            )
    return errors


def _validate_legacy(beats: list[dict], path: Path) -> list[str]:
    """The seven collection-level checks, unchanged (`validate()`'s pre-dispatch
    behavior). An empty beats list passes trivially."""
    return (
        _check_hash_uniqueness(beats)
        + _check_beat_id_uniqueness(beats)
        + _check_identity_uniqueness(beats)
        + _check_wikipedia_grounding(beats, path.parent / "wikipedia")
        + _check_book_grounding(beats, path)
        + _check_verification_freshness(beats)
        + _check_status_vocabulary(beats)
    )


def _record_shape(record: object) -> str:
    """Per-record shape for FILE-level dispatch: 'new', 'legacy', or 'unknown'.

    Structural, per decisions.shape_detection: both `claims` and `narration`
    present -> 'new'; exactly one, or a non-dict record -> 'unknown'; never
    keys on `script_body`. Neither present -> 'legacy' ONLY when the record
    carries something beyond a bare `beat_id` — a record that is nothing but
    `{"beat_id": ...}` (or empty) is indistinguishable from a truncated
    anything and is 'unknown' rather than a guessed 'legacy'. A real legacy
    beat always carries its old-schema fields (poi_name, script_body_hash,
    ...) alongside beat_id, so this never affects real corpus data.
    """
    if not isinstance(record, dict):
        return "unknown"
    has_claims = "claims" in record
    has_narration = "narration" in record
    if has_claims and has_narration:
        return "new"
    if has_claims or has_narration:
        return "unknown"
    if set(record) - {"beat_id"}:
        return "legacy"
    return "unknown"


def _shape_label(record: object, index: int) -> str:
    if isinstance(record, dict):
        beat_id = record.get("beat_id")
        if isinstance(beat_id, str) and beat_id:
            return beat_id
    return f"#{index}"


def _dispatch_shape(beats: list[dict]) -> tuple[str, list[str]]:
    """Classify the whole file's shape. Returns (shape, refusal_errors):
    `shape` is 'new' or 'legacy' to dispatch to a validator, or 'refused'
    with `refusal_errors` already filled in (SHAPE_MIXED or SHAPE_UNKNOWN
    lines) when the file isn't cleanly one shape (decisions.shape_detection
    — "the CLI refuses a mixed file with SHAPE_MIXED").
    """
    if not beats:
        return "legacy", []
    per_record = [(i, b, _record_shape(b)) for i, b in enumerate(beats)]
    shapes_present = {shape for _, _, shape in per_record}
    if shapes_present == {"new"}:
        return "new", []
    if shapes_present == {"legacy"}:
        return "legacy", []
    unknowns = [(i, b) for i, b, shape in per_record if shape == "unknown"]
    if unknowns:
        return "refused", [
            f"SHAPE_UNKNOWN {_shape_label(b, i)}: record shape is neither new nor legacy"
            for i, b in unknowns
        ]
    return "refused", [
        "SHAPE_MIXED: file mixes new-shape (claims+narration) and legacy-shape beat "
        "records — a beats.json must be entirely one shape or the other"
    ]


def _derive_chunks_root(beats_path: Path) -> Path | None:
    """`Books/{City}` for `beats_path`, matched case-insensitively — the same
    derivation the legacy book-grounding gate uses (decisions.chunks_root):
    `data/{city}/beats.json` -> repo root -> `Books/{City}`. Returns None
    when unlocatable; the caller turns that into a hard error, never a
    soft-skip, on the new-shape path.
    """
    resolved = beats_path.resolve()
    books_root = resolved.parent.parent.parent / "Books"
    if not books_root.exists():
        return None
    city_slug = resolved.parent.name
    return next(
        (d for d in books_root.iterdir() if d.is_dir() and d.name.lower() == city_slug.lower()),
        None,
    )


class ChunksRootUnresolvableError(ValueError):
    """A new-shape file needs a chunks root and none was found or given."""


def validate(path: Path, chunks_root: str | Path | None = None) -> list[str]:
    """Validate a beats.json file, dispatching on shape (module docstring).

    Kept as a single positional-argument-compatible entry point: every
    existing caller passing only `path` keeps working unchanged (it only
    ever sees legacy-shape data today). `chunks_root` is used, and may be
    derived, only on the new-shape path; the legacy path ignores it.
    """
    beats = _load_beats(path)
    shape, refusal = _dispatch_shape(beats)
    if shape == "refused":
        return refusal
    if shape == "legacy":
        return _validate_legacy(beats, path)
    # shape == "new"
    root = Path(chunks_root) if chunks_root is not None else _derive_chunks_root(path)
    if root is None:
        raise ChunksRootUnresolvableError(
            f"cannot locate a chunks root for {path} — pass --chunks-root <dir> "
            "(new-shape beats need it for span grounding)"
        )
    return ingest_model.validate(beats, chunks_root=root)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="validate_beats.py")
    parser.add_argument("path", help="path to a beats.json file")
    parser.add_argument(
        "--chunks-root",
        default=None,
        help="chunk source root for new-shape span grounding (else derived from path)",
    )
    args = parser.parse_args(argv[1:])
    path = Path(args.path)
    try:
        errors = validate(path, chunks_root=args.chunks_root)
    except ChunksRootUnresolvableError as exc:
        print(f"validate_beats: {exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"validate_beats: cannot read {path}: {exc}", file=sys.stderr)
        return 2

    if errors:
        print(f"validate_beats: FAIL ({len(errors)} issue(s)) in {path}")
        for line in errors:
            print(f"  {line}")
        return 1
    shape, _ = _dispatch_shape(_load_beats(path))
    print(f"validate_beats: PASS shape={shape} ({path})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
