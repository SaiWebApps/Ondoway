#!/usr/bin/env python
"""Programmatic gates for `/unified-beat-extract` (B11 + B12).

Three honor-system rules are now mechanical:

1. **Source-span gate (B12)** — count contiguous factual sentences in the
   cited source_passage; cap `beat_length_class`. Honors the
   semicolon-clause rule from the prompt patch.
2. **Length-class enforcer** — word count vs class range, with a
   re-class suggestion when a beat drifts outside its declared band.
3. **Fabrication probe (B11 honesty)** — extract concrete claims (years,
   proper-noun phrases, specific quantities) from `script_body` and from
   each `physical_cues[].cue` text; flag any that don't appear in the
   cited `source_passage` AND don't appear anywhere in the broader
   `chunk_text`. Returns the suggested `extractor_state` plus a list of
   strings to drop into `flagged_claims`.

The probe is heuristic — it can't catch every paraphrase drift. But it
catches the high-yield patterns that drove fabrication in the
2026-05-01 Parisians chunk-13 v1 run (1869, 1957 replacement, December
centenary, Communard column-pulldown, Henri IV 1605, etc.).

`validate_beat()` orchestrates all three checks and returns a structured
verdict the extractor can act on before `scripts.beats_io.commit`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from scripts.verbatim import VERBATIM_RUN_BLOCK, run_outside_quotation

# ─── B12 source-span gate ────────────────────────────────────────────────

# A "factual sentence" boundary is a period/question/exclamation OR a
# semicolon followed by a clause that introduces a new factual claim.
# Heuristic: split on ".!?;" while keeping the boundary, drop empty/whitespace-only
# fragments, drop fragments that don't contain ≥3 words.
_SENT_SPLIT_RE = re.compile(r"[.!?;]+\s+")


def count_source_sentences(source_passage: str) -> int:
    """Count factual-sentence units in a source_passage.

    Per B12 (post-2026-05-01 patch), semicolon-joined clauses each carrying
    an independent factual claim count as separate sentences. We approximate
    this by splitting on terminal punctuation OR semicolons and dropping
    fragments that don't carry ≥3 words (filter ellipses, headings,
    "I.A.i."-style numbering, etc.).
    """
    if not source_passage or not source_passage.strip():
        return 0
    text = source_passage.strip()
    # Strip leading numbering like "I.A.i." or "II ."
    text = re.sub(r"^[IVX]+\.[A-Za-z]?\.?[ivx]?\.?\s+", "", text)
    fragments = _SENT_SPLIT_RE.split(text)
    # Append the final fragment; if the passage ended with terminal punctuation,
    # _SENT_SPLIT_RE leaves an empty trailing item which we drop.
    count = 0
    for frag in fragments:
        # Count "real" words (alphabetic runs ≥2 letters)
        real_words = re.findall(r"[A-Za-zÀ-ÿ]{2,}", frag)
        if len(real_words) >= 3:
            count += 1
    return count


def source_span_gate(source_passage: str) -> str:
    """Return the maximum allowed `beat_length_class` for a given source span.

    - 2 or fewer source sentences -> 'seasoning' (with 'micro' implicit fallback)
    - 3-5 source sentences -> 'mid'
    - 6+ source sentences -> 'anchor' (anchor in play; mid still legal)
    """
    n = count_source_sentences(source_passage)
    if n <= 2:
        return "seasoning"
    if n <= 5:
        return "mid"
    return "anchor"


_CLASS_RANGES: dict[str, tuple[int, int]] = {
    "anchor": (200, 400),
    "mid": (80, 200),
    "seasoning": (20, 80),
    "micro": (0, 20),
}


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def check_length_class(script_body: str, beat_length_class: str) -> tuple[bool, str | None]:
    """Verify body word count fits the declared class.

    Returns `(in_range, suggested_reclass)`:
      - `(True, None)` if word count is inside the class range.
      - `(False, suggested)` where `suggested` is the class whose range
        the actual word count falls into. Caller decides whether to
        re-class down (preferred per the spec) or expand prose from
        source.
    """
    wc = word_count(script_body)
    lo, hi = _CLASS_RANGES.get(beat_length_class, (0, 10**9))
    if lo <= wc <= hi:
        return True, None
    for cls, (clo, chi) in _CLASS_RANGES.items():
        if clo <= wc <= chi:
            return False, cls
    return False, None


# ─── B11 fabrication probe ────────────────────────────────────────────────


def _normalize(s: str) -> str:
    """Curly→straight quotes, em/en-dashes→hyphens, ellipsis→..., NFKC,
    collapse whitespace, lowercase. Used for substring matching only —
    never for hash uniqueness."""
    if not s:
        return ""
    s = (
        s.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2026", "...")
    )
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", " ", s).lower()


# Concrete-claim extractors — patterns that imply a specific verifiable fact.
_YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
_PROPER_NOUN_RE = re.compile(
    # The curly apostrophe is data — French names carry it — so it is escaped rather
    # than typed, which is how the rest of this repo writes one inside a pattern.
    r"\b(?:[A-Z\u00c0-\u00dd][a-z\u00e0-\u00ff'\u2019\-]+"
    r"(?:\s+(?:de|of|du|des|le|la|les|von|van|d['\u2019]))?\s+)+"
    r"[A-Z\u00c0-\u00dd][a-z\u00e0-\u00ff'\u2019\-]+\b"
)
# Sentence-start function words that get capitalized but are not entity-like.
# Stripping these from the start of a candidate kills the "In April" / "Once
# Guillaumot" / "Through 1777" / "When the Rue d'Enfer" false positives without
# losing real entity capture.
_SENTENCE_START_FUNCTION_WORDS = {
    "the",
    "in",
    "on",
    "at",
    "by",
    "to",
    "from",
    "of",
    "for",
    "with",
    "once",
    "now",
    "then",
    "after",
    "before",
    "through",
    "when",
    "while",
    "as",
    "since",
    "until",
    "during",
    "having",
    "this",
    "that",
    "those",
    "these",
    "a",
    "an",
    "next",
    "later",
    "earlier",
    "above",
    "below",
    "outside",
    "inside",
    "behind",
    "beside",
    "between",
    "but",
    "and",
    "or",
    "yet",
    "so",
    "however",
}
# Imported-context red flags — phrases that frequently arrive from world
# knowledge, not the source. Curated from the 2026-05-01 chunk-13 audit.
_RED_FLAG_TERMS = [
    "replica",
    "replacement",
    "rebuilt",
    "demolished",
    "restored in",
    "burned in",
    "burnt in",
    "destroyed in",
    "now in",
    "currently in",
    "today the",
    "as of",
]


@dataclass
class FabricationVerdict:
    unsourced_claims: list[str] = field(default_factory=list)
    cue_unsourced: list[tuple[int, str]] = field(default_factory=list)
    has_fabrication: bool = False

    def merge_into_beat(self) -> dict[str, Any]:
        """Return the patch dict to apply to a beat: extractor_state +
        flagged_claims additions."""
        if not self.has_fabrication:
            return {}
        claims = list(self.unsourced_claims)
        for idx, claim in self.cue_unsourced:
            claims.append(f"physical_cues[{idx}]: {claim}")
        return {
            "extractor_state": "imported_context",
            "additional_flagged_claims": claims,
        }


def _candidate_claims(text: str) -> list[str]:
    """Pull out concrete claim candidates from a body or cue: years,
    multi-word proper nouns, and red-flag phrases.

    Each candidate is returned with whatever surrounding clause is most
    informative for the user reading `flagged_claims`. We bias toward
    over-reporting on this side — false positives just become more entries
    in `flagged_claims` that the user can dismiss; false negatives are
    silent fabrication, which is the failure mode we exist to prevent.
    """
    out: list[str] = []
    # Years
    for m in _YEAR_RE.finditer(text):
        # Anchor a ±50-char window around the year for context
        start = max(0, m.start() - 50)
        end = min(len(text), m.end() + 50)
        snippet = text[start:end].strip()
        out.append(f"year {m.group()} in: '{snippet}'")
    # Multi-word proper nouns (trim duplicates while preserving order).
    # Strip leading sentence-start function words ("The Excavation" → "Excavation",
    # "Once Guillaumot" → "Guillaumot") so we don't false-flag captures that are
    # really just a connective + a sourced entity. Strip trailing verb-suffix
    # tokens too ("Saint Denis Christianised" → "Saint Denis"); the regex
    # over-captures verbs that follow a name.
    seen: set[str] = set()
    for m in _PROPER_NOUN_RE.finditer(text):
        phrase = m.group().strip()
        words = phrase.split()
        # Drop leading function words.
        while words and words[0].lower() in _SENTENCE_START_FUNCTION_WORDS:
            words = words[1:]
        # Drop trailing verb-like tokens (-ed, -ing, -ised, -ized, -ing endings).
        while words and re.search(r"(?:ed|ing|ised|ized)$", words[-1].lower()):
            words = words[:-1]
        if len(words) < 2:
            continue
        clean = " ".join(words)
        if clean.lower() not in seen:
            seen.add(clean.lower())
            out.append(f"named entity: '{clean}'")
    # Red-flag phrase markers
    nt = _normalize(text)
    for term in _RED_FLAG_TERMS:
        if term in nt:
            # Find the first occurrence in the original text (case-insensitive)
            mm = re.search(re.escape(term), text, re.IGNORECASE)
            if mm:
                start = max(0, mm.start() - 30)
                end = min(len(text), mm.end() + 60)
                out.append(f"red-flag phrase '{term}' in: '{text[start:end].strip()}'")
    return out


_DIALECT_FOLD_RE = re.compile(r"ised\b|izing\b|isation\b|ising\b")
_QUOTE_STRIP_RE = re.compile(r"['\"]")


def _fold_for_match(s: str) -> str:
    """Make British/American spellings comparable, and strip quote characters
    that get embedded around named entities (e.g. Robb's `the 'Excavation'
    team`). Used only for the substring-in-source check, never for emission."""
    s = _DIALECT_FOLD_RE.sub(
        lambda m: {"ised": "ized", "izing": "izing", "isation": "ization", "ising": "izing"}[
            m.group()
        ],
        s,
    )
    return _QUOTE_STRIP_RE.sub("", s)


def _claim_in(claim: str, source_norm: str, chunk_norm: str) -> bool:
    """Return True if the *core* of the claim appears in either source_passage
    or chunk_text. The 'core' is the year, the proper noun, or the red-flag
    phrase — not the whole context window."""
    # Year claims: extract the 4-digit year
    year = _YEAR_RE.search(claim)
    if year:
        y = year.group()
        return y in source_norm or y in chunk_norm
    # Named-entity claims: extract the quoted phrase. Compare with quote
    # characters stripped (so `the 'Excavation' team` matches `the Excavation
    # team`) and dialect-folded (so `Christianised` matches `Christianized`).
    m = re.search(r"'([^']+)'", claim)
    if m:
        phrase = _fold_for_match(_normalize(m.group(1)))
        src = _fold_for_match(source_norm)
        chk = _fold_for_match(chunk_norm)
        return phrase in src or phrase in chk
    # Red-flag phrase claims: same extraction
    return False


def fabrication_probe(
    *,
    script_body: str,
    physical_cues: list[dict] | None,
    source_passage: str,
    chunk_text: str,
) -> FabricationVerdict:
    """Detect concrete claims in body/cues that don't appear in the
    cited source_passage AND don't appear in the broader chunk_text.

    The chunk_text fallback is important: an extractor may legitimately
    cite a narrower source_passage than the full claim-supporting span
    in the chunk, especially for transit beats. The chunk_text check
    avoids false-flagging those.
    """
    src_n = _normalize(source_passage)
    chunk_n = _normalize(chunk_text)

    # Body claims
    body_candidates = _candidate_claims(script_body)
    body_unsourced = [c for c in body_candidates if not _claim_in(c, src_n, chunk_n)]

    # Cue claims
    cue_unsourced: list[tuple[int, str]] = []
    for idx, cue in enumerate(physical_cues or []):
        cue_text = cue.get("cue", "") if isinstance(cue, dict) else str(cue)
        for c in _candidate_claims(cue_text):
            if not _claim_in(c, src_n, chunk_n):
                cue_unsourced.append((idx, c))

    has_fab = bool(body_unsourced or cue_unsourced)
    return FabricationVerdict(
        unsourced_claims=body_unsourced,
        cue_unsourced=cue_unsourced,
        has_fabrication=has_fab,
    )


# ─── Source-grounding gate ──────────────────────────────────────────────
# B12 and the fabrication probe both trust `source_passage` as ground truth;
# nothing verifies the passage itself was quoted from the chunk. A 2026-05-28
# Wikipedia run produced beats whose source_passage was reconstructed from
# model memory (wrong facts, absent from the pinned file) yet cleared every
# other gate. This gate closes that hole: each contiguous span the passage
# claims to quote must actually appear in chunk_text.

_ELLIPSIS_RE = re.compile(r"\.{3}|…")


def _grounding_fragments(source_passage: str) -> list[str]:
    """Split a source_passage into the contiguous spans it claims to quote.

    Splits on sentence terminators AND ellipsis (an elision marker separates
    two independently-quoted spans). Keeps only fragments with ≥4 real words,
    so trivial fragments and headings don't dominate the ratio.
    """
    text = _ELLIPSIS_RE.sub(" ", source_passage or "")
    out: list[str] = []
    for frag in _SENT_SPLIT_RE.split(text):
        if len(re.findall(r"[A-Za-zÀ-ÿ]{2,}", frag)) >= 4:
            out.append(frag.strip())
    return out


def source_grounding_gate(source_passage: str, chunk_text: str) -> tuple[int, list[str]]:
    """Return (total_substantial_fragments, ungrounded_fragments).

    A fragment is grounded if its normalized form is a substring of the
    normalized chunk_text. An empty chunk (can't assess) yields no ungrounded
    fragments so the gate never blocks on missing source.
    """
    chunk_n = _normalize(chunk_text)
    fragments = _grounding_fragments(source_passage)
    if not chunk_n:
        return len(fragments), []
    ungrounded = [f for f in fragments if _normalize(f) not in chunk_n]
    return len(fragments), ungrounded


# ─── Copying gate ────────────────────────────────────────────────────────
# The fabrication probe asks whether a beat says more than its source. This asks
# whether it says the same thing in the same words. They are different defects and
# a beat can carry either alone: prose lifted whole is perfectly faithful.


def copying_gate(script_body: str, source_passage: str) -> tuple[int, str]:
    """The longest run this body shares with its source, and the words of it.

    Measured outside attributed quotation, so a beat that quotes a named speaker and
    says who said it is not blocked for the quote. A quotation attributed to nobody
    is not exempt — that is what an unmarked lift looks like.

    Returns `(length, text)` so a refusal can show the run rather than assert one.
    """
    found = run_outside_quotation(script_body or "", source_passage or "")
    return found["length"], found["text"]


# ─── Orchestration ────────────────────────────────────────────────────────


@dataclass
class BeatVerdict:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggested_class: str | None = None
    fabrication: FabricationVerdict | None = None
    #: The longest run shared with the source outside quotation, on every beat —
    #: recorded whether or not it crossed the block, so a chunk can report its
    #: distribution rather than only its failures.
    copied_run: int = 0
    copied_text: str = ""


def validate_beat(beat: dict, chunk_text: str) -> BeatVerdict:
    """Run all three programmatic gates against a single beat.

    `ok=False` when an error is present (length-class out of range that
    can't be auto-fixed, or a hard B12 violation). Warnings + fabrication
    findings populate even when `ok=True` — the caller decides how strict
    to be.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # B12 source-span gate
    span_max = source_span_gate(beat.get("source_passage", ""))
    declared = beat.get("beat_length_class", "")
    rank = ["micro", "seasoning", "mid", "anchor"]
    if rank.index(declared) > rank.index(span_max):
        errors.append(
            f"B12 violation: beat_length_class={declared!r} exceeds source-span ceiling "
            f"of {span_max!r} ({count_source_sentences(beat.get('source_passage', ''))} "
            f"source sentences). Re-class down or cite more source."
        )

    # Source-grounding gate: source_passage must be quoted from chunk_text.
    # Fire only when ≥2 fragments AND >30% are ungrounded — a single
    # artifact-broken fragment (mid-sentence page break, OCR noise) in a short
    # passage must not hard-block an otherwise-honest beat, but wholesale
    # reconstruction from memory (the failure this gate exists for) misses
    # most or all fragments and trips both conditions.
    total_frags, ungrounded = source_grounding_gate(beat.get("source_passage", ""), chunk_text)
    if len(ungrounded) >= 2 and len(ungrounded) / total_frags > 0.3:
        errors.append(
            f"source-grounding violation: {len(ungrounded)}/{total_frags} source_passage "
            f"sentence(s) do not appear in chunk_text — the passage was not quoted from the "
            f"pinned source (likely reconstructed from memory). "
            f"First ungrounded: {ungrounded[0][:90]!r}"
        )

    # Length-class word count
    in_range, suggested = check_length_class(beat.get("script_body", ""), declared)
    if not in_range:
        wc = word_count(beat.get("script_body", ""))
        warnings.append(
            f"length-class drift: body is {wc}w but {declared!r} expects "
            f"{_CLASS_RANGES.get(declared)}. Suggested re-class: {suggested!r}."
        )

    # Copying gate. An error, not a warning: a lifted beat is not a beat that needs
    # a note on it, it is one that cannot ship. The extractor is holding the source,
    # so naming the run it must not reuse costs nothing and is what the retry needs.
    run_len, run_text = copying_gate(beat.get("script_body", ""), beat.get("source_passage", ""))
    if run_len >= VERBATIM_RUN_BLOCK:
        errors.append(
            f"copying violation: {run_len} consecutive words shared with the source "
            f"outside an attributed quotation (block is {VERBATIM_RUN_BLOCK}). "
            f"Write the fact in your own sentence, or attribute and quote it. "
            f"The run: {run_text!r}"
        )

    # Fabrication probe
    fab = fabrication_probe(
        script_body=beat.get("script_body", ""),
        physical_cues=beat.get("physical_cues", []),
        source_passage=beat.get("source_passage", ""),
        chunk_text=chunk_text,
    )
    if fab.has_fabrication:
        cur_state = beat.get("fact_check", {}).get("extractor_state", "clean")
        if cur_state == "clean":
            warnings.append(
                f"fabrication probe flagged {len(fab.unsourced_claims)} body claim(s) "
                f"+ {len(fab.cue_unsourced)} cue claim(s). Either drop the unsourced "
                f"clauses or set extractor_state='imported_context' and add to "
                f"flagged_claims."
            )

    return BeatVerdict(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        suggested_class=suggested if not in_range else None,
        fabrication=fab if fab.has_fabrication else None,
        copied_run=run_len,
        copied_text=run_text,
    )
