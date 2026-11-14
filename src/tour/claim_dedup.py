"""Route-level claim-repetition suppression.

A route can seat several beats — often from DIFFERENT source books — that restate
the same historical fact. The canonical case: the Île de la Cité seats three
beats (from Around-and-About, Rough Guide, Frommer's) that each narrate "the
Parisii settled the island in the 3rd century BC", so the tourist hears the
founding story three times. The exact-beat-id dedup in ``generation.py`` cannot
catch this (different ids), and the within-POI ``_apply_b8_lite_dedup`` misses it
too (cross-book paraphrases with BC/century dates and divergent supporting-entity
casts evade its 4-digit-year + entity thresholds).

This pass runs over the ASSEMBLED sentence stream and drops a beat-sourced
sentence ONLY when EVERY ``key_claim`` that sentence realizes was already voiced
by an EARLIER, DIFFERENT beat anywhere in the route. It is deliberately surgical:

- CONTENT-SAFE by construction: a sentence that fuses a repeated fact with a
  NOVEL one (compound sentences are common — "settled c.300 BC by the Parisii,
  and in 52 BC overrun by the Romans, who built a palace-fortress") is kept
  INTACT, because it still realizes a novel claim. Only a PURE restatement — a
  sentence whose every claim is already voiced — is removed, so a distinct fact
  is never silently lost. (The trade: a compound restatement may voice a
  duplicated fact a second time; that is accepted to guarantee zero content loss.
  A beat that ALSO carries distinct claims keeps every sentence carrying one.)
- It NEVER empties a beat: at least one sentence per beat always survives, so the
  set of emitted beat-ids is unchanged and the golden overlap gate is not
  perturbed (it counts distinct beat source-ids, not sentences).
- It only touches ``source_type == "beat"`` sentences; glue, synthesized openers
  and reflections are never removed.

Reported-audio honesty: ``_sum_audio`` now scales each cited beat by its
SURVIVING-sentence fraction, so a trimmed beat reports only what it voiced (a
non-deduped beat keeps all sentences -> no change). ``verify._visited_claims``
keys off the whole beat's ``key_claims``, but that is NOT a dedup leftover: this
pass always keeps the FIRST occurrence of a claim, so any claim a later
reflection cites was voiced at least once — the pre-existing key_claims-vs-voiced
mismatch is independent of dedup. (Residual: ``_flatten_pois`` per-stop
``dwell_seconds`` still measures the pre-dedup plan — a small per-stop display
over-report on the rare deduped stop; the tour TOTAL is honest.)

Detection is deterministic (no build-time LLM): each sentence is matched to the
beat's best key_claim; two claims are "the same fact" when their canonical-token
signatures have high overlap. Dates are canonicalized to centuries so
"300 BC" and "the 3rd century BC" unify.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .contract import BeatRef, BeatSequence, Script, Sentence

# A sentence must contain at least this fraction of a claim's tokens to be judged
# "about" that claim (overlap coefficient, so ~containment of the terse claim).
CLAIM_MATCH_MIN: float = 0.5
# COVERAGE uses a LOWER floor than the dedup pass. Coverage only guards against
# GROSS deletion — a fact mentioned in NO sentence — and its counterpart, the
# semantic faithfulness gate, already blocks a fusion that quietly loses a fact.
# A bold fusion rewords a claim heavily, dropping its lexical overlap below 0.5
# though the fact is plainly present; at 0.5 coverage false-flags that as a drop
# and reverts the whole (faithful) stop. 0.34 keeps gross deletions caught.
COVERAGE_MATCH_MIN: float = 0.34
# Two claim signatures are the SAME fact at or above this overlap coefficient.
CLAIM_DEDUP_THRESHOLD: float = 0.7
# ...and must share at least this many tokens (guards against tiny-claim noise).
MIN_SHARED_TOKENS: int = 2

_STOPWORD_TEXT = (
    "the a an and or but of to in on at by for with from into onto over under as is "
    "was were are be been being it its this that these those their there here who whom "
    "which what when where why how then than so such not no nor only just also very can "
    "could would should may might must will shall do does did done has have had they "
    "them he she his her our your my mine you we us me one two first now still around "
    "about near between along after before during within upon out off up down"
)
_STOPWORDS: frozenset[str] = frozenset(_STOPWORD_TEXT.split())

# Ordinal century, e.g. "3rd century BC" -> c3bc ; "13th century" -> c13ad.
_ORD_CENTURY_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+century(\s+bc)?\b", re.IGNORECASE)
# Bare year with era, e.g. "300 BC" -> its century ; "52 BC" -> c1bc.
_YEAR_BC_RE = re.compile(r"\b(\d{1,4})\s*bc\b", re.IGNORECASE)


def _canonicalize_dates(text: str) -> str:
    """Fold date phrasings to a shared century token so paraphrases unify.

    "300 BC" and "the 3rd century BC" both -> "c3bc"; "52 BC" -> "c1bc";
    "13th century" -> "c13ad". 4-digit AD years are left as their own digit token.
    """

    def _ord(match: re.Match[str]) -> str:
        n = int(match.group(1))
        era = "bc" if match.group(2) else "ad"
        return f" c{n}{era} "

    def _bc_year(match: re.Match[str]) -> str:
        n = int(match.group(1))
        century = (n - 1) // 100 + 1
        return f" c{century}bc "

    text = _ORD_CENTURY_RE.sub(_ord, text)
    text = _YEAR_BC_RE.sub(_bc_year, text)
    return text


def _signature(text: str) -> frozenset[str]:
    """Canonical salient-token set of a claim or sentence."""
    text = _canonicalize_dates(text.lower())
    tokens = re.findall(r"[a-z0-9]+", text)
    return frozenset(t for t in tokens if len(t) >= 3 and t not in _STOPWORDS)


def _overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """Overlap coefficient: |a∩b| / min(|a|,|b|). 0 if either is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _realized_claim_signatures(
    sentence_text: str, claim_signatures: list[frozenset[str]]
) -> list[frozenset[str]]:
    """Every key_claim signature this sentence realizes (overlap ≥ CLAIM_MATCH_MIN).

    A compound sentence realizes MORE THAN ONE claim; we must see all of them so a
    novel claim riding alongside a repeated one keeps the sentence alive.
    """
    s_sig = _signature(sentence_text)
    if not s_sig:
        return []
    # fraction of each terse claim present in the sentence (~containment)
    return [c_sig for c_sig in claim_signatures if _overlap(c_sig, s_sig) >= CLAIM_MATCH_MIN]


def suppress_repeated_claims(
    sentences: list[Sentence],
    beat_sequence: BeatSequence,
    *,
    include_same_beat: bool = False,
) -> list[Sentence]:
    """Drop beat sentences that restate a claim already voiced by an earlier beat.

    Pure. Preserves order, never empties a beat, and only ever removes
    ``source_type == "beat"`` sentences.

    ``include_same_beat`` (default off, so the pre-compose stitch behaves exactly
    as before): also drop a sentence that merely restates a claim already voiced by
    an EARLIER sentence of its OWN beat. The composer (over a big multi-beat stop)
    can echo one beat as both a polished paraphrase and a near-verbatim retelling —
    same ``source_id``, same claim, different wording — which the cross-beat-only
    rule leaves in place, and which neither ``suppress_exact_repeats`` (not byte-
    identical) catches. On the composed output we turn this on so the fact reads
    once. Still coverage-safe: a beat sentence carrying a NOVEL claim is always
    kept, and the never-empty-a-beat restore below guarantees every beat keeps ≥1.
    """
    beats_by_id = {b.id: b for plan in beat_sequence.poi_beats for b in plan.beats}
    # Vignette (walk-past) beats are voiced as real beat sentences too, so they must
    # participate in cross-beat dedup — a walk-past one-liner that merely restates an
    # earlier stop's claim would otherwise be voiced verbatim (the tourist hears the
    # fact twice), exactly the cross-beat repetition this pass suppresses.
    vignette_ids = {b.id for beats in beat_sequence.vignette_beats.values() for b in beats}
    for beats in beat_sequence.vignette_beats.values():
        for b in beats:
            beats_by_id.setdefault(b.id, b)
    claim_sigs_by_beat: dict[str, list[frozenset[str]]] = {
        bid: [sig for c in b.key_claims if (sig := _signature(c)) and len(sig) >= MIN_SHARED_TOKENS]
        for bid, b in beats_by_id.items()
    }

    voiced: list[tuple[str, frozenset[str]]] = []  # (beat_id, claim_sig) from kept sentences

    def _already_voiced(sig: frozenset[str], own_beats: tuple[str, ...]) -> bool:
        return any(
            (include_same_beat or bid not in own_beats)
            and _overlap(sig, vsig) >= CLAIM_DEDUP_THRESHOLD
            and len(sig & vsig) >= MIN_SHARED_TOKENS
            for bid, vsig in voiced
        )

    drop: set[int] = set()
    for i, s in enumerate(sentences):
        if s.source_type != "beat":
            continue
        # Judge the sentence against the UNION of ALL its cited beats' claims
        # (source_id + also_cites): a FUSED cross-beat sentence carries the also_cited
        # beat's fact too, so it must NOT be dropped as a "pure restatement" of its
        # primary beat alone — that silently empties the also_cited beat.
        cited = s.cited_beat_ids
        claim_sigs = [sig for bid in cited for sig in claim_sigs_by_beat.get(bid, [])]
        if not claim_sigs:
            continue
        realized = _realized_claim_signatures(s.text, claim_sigs)
        if not realized:
            continue
        novel = [sig for sig in realized if not _already_voiced(sig, cited)]
        if not novel:
            # PURE restatement — every claim it realizes is already voiced elsewhere.
            drop.add(i)
        else:
            # A novel claim keeps the sentence; record it so later beats can dedup.
            voiced.extend((s.source_id, sig) for sig in novel)

    if not drop:
        return sentences

    # Never empty a SEATED beat: if every one of a dwell beat's sentences was
    # dropped, restore its first dropped sentence so the emitted beat-id set (and
    # goldens) hold. Vignette walk-past beats are EXEMPT — they are additive
    # annotations, not seated stops, so a vignette that is entirely a restatement is
    # dropped whole rather than voiced as a duplicate.
    # A beat is "kept" if any SURVIVING sentence cites it (primary or also_cites), so
    # an also_cited beat carried only by a fused sentence counts as voiced.
    kept_by_beat: dict[str, int] = defaultdict(int)
    for i, s in enumerate(sentences):
        if s.source_type == "beat" and i not in drop:
            for bid in s.cited_beat_ids:
                kept_by_beat[bid] += 1
    for i, s in enumerate(sentences):
        if s.source_type != "beat" or i not in drop:
            continue
        # Restore this dropped sentence if it is the last carrier of any SEATED
        # (non-vignette) cited beat — so no beat, primary OR also_cited, is emptied.
        if any(bid not in vignette_ids and kept_by_beat[bid] == 0 for bid in s.cited_beat_ids):
            drop.discard(i)
            for bid in s.cited_beat_ids:
                kept_by_beat[bid] = max(kept_by_beat[bid], 1)

    return [s for i, s in enumerate(sentences) if i not in drop]


def _repeat_key(text: str) -> str:
    """Normalized form for exact-repeat detection: lowercased, whitespace-
    collapsed, surrounding quotes/spaces stripped."""
    return re.sub(r"\s+", " ", text.strip().strip("\"'“” ").lower()).strip()


def suppress_exact_repeats(
    sentences: list[Sentence], beat_sequence: BeatSequence
) -> list[Sentence]:
    """Drop a beat sentence byte-identical (normalized) to an EARLIER beat
    sentence in the SAME stop.

    A verbatim restatement carries zero new information, so this is always
    content-safe — the FIRST occurrence is kept. It catches the gross literal
    repeats the claim-based pass leaves behind: two beats seated at one POI that
    share an identical sentence (the workbench "duplicated in the beats" report),
    which ``suppress_repeated_claims`` misses when the sentence maps to no
    ``key_claim``. Runs WITHIN a stop only (a repeated framing across distant
    stops is left to the claim pass). Never empties a seated beat — vignette
    walk-past beats are exempt (additive annotations).
    """
    vignette_ids = {b.id for beats in beat_sequence.vignette_beats.values() for b in beats}
    seen_by_stop: dict[int, set[str]] = defaultdict(set)
    drop: set[int] = set()
    for i, s in enumerate(sentences):
        if s.source_type != "beat":
            continue
        key = _repeat_key(s.text)
        if len(key) < 25:  # too short to be a meaningful restatement
            continue
        if key in seen_by_stop[s.stop_idx]:
            drop.add(i)
        else:
            seen_by_stop[s.stop_idx].add(key)
    if not drop:
        return sentences
    # Never empty a SEATED beat: restore its first dropped sentence if every one of
    # its sentences was a duplicate (keeps the emitted beat-id set stable). By SOURCE
    # ID only — NOT cited_beat_ids: a round-4 red-team proved a cited-beat restore here
    # RESURRECTS a byte-identical duplicate (the exact bug this system prevents). An
    # also_cited beat whose sole carrier is a duplicate is either already covered by
    # its verbatim twin, or a genuine loss the (keyless-aware) coverage gate catches.
    kept_by_beat: dict[str, int] = defaultdict(int)
    for i, s in enumerate(sentences):
        if s.source_type == "beat" and i not in drop:
            kept_by_beat[s.source_id] += 1
    for i, s in enumerate(sentences):
        if (
            s.source_type == "beat"
            and s.source_id not in vignette_ids
            and kept_by_beat[s.source_id] == 0
            and i in drop
        ):
            drop.discard(i)
            kept_by_beat[s.source_id] = 1
    return [s for i, s in enumerate(sentences) if i not in drop]


# A composer echo of one beat as two full re-tellings is near-verbatim but rarely
# byte-identical; this ratio catches it. Kept high so only a genuine restatement of
# the SAME beat is dropped — a distinct fact reads well below it (the Abelard echo
# measured 98-100; two different facts measured ~37).
_NEAR_DUP_RATIO = 90.0


def suppress_same_beat_near_duplicates(
    sentences: list[Sentence], threshold: float = _NEAR_DUP_RATIO
) -> list[Sentence]:
    """Drop a beat sentence that is a near-verbatim restatement (rapidfuzz
    ``token_set_ratio >= threshold``) of an EARLIER sentence of its OWN beat.

    Catches the composer voicing one beat as TWO full re-tellings — same
    ``source_id``, near-identical wording, which the claim-based pass misses when
    the re-telling drifts from the ``key_claims`` tokens (or the beat has none), and
    which ``suppress_exact_repeats`` misses because it is not byte-identical.
    Restricted to the SAME ``source_id`` so a genuinely DISTINCT fact — a different
    beat that happens to read similarly — is NEVER dropped. Pure; preserves order;
    only removes ``source_type == 'beat'``; never empties a beat (the first telling
    of each beat has no earlier same-beat sentence, so it is always kept).
    """
    from rapidfuzz import fuzz

    kept_keys_by_beat: dict[str, list[str]] = defaultdict(list)
    drop: set[int] = set()
    for i, s in enumerate(sentences):
        if s.source_type != "beat":
            continue
        key = _repeat_key(s.text)
        if len(key) < 25:  # too short to judge as a restatement
            continue
        earlier = kept_keys_by_beat[s.source_id]
        if any(fuzz.token_set_ratio(key, e) >= threshold for e in earlier):
            drop.add(i)
        else:
            earlier.append(key)
    # NO also_cites never-empty restore here (a round-4 red-team proved it RESURRECTS a
    # near-verbatim duplicate — the exact bug this system prevents). A fused also_cited
    # beat whose only carrier is a near-dup is either covered by its near-identical twin
    # or a genuine loss the (keyless-aware) coverage gate catches and repairs.
    return [s for i, s in enumerate(sentences) if i not in drop]


def _claims_for_coverage(beat: BeatRef) -> tuple[str, ...]:
    """The claims a beat is expected to voice for the COVERAGE baseline: its
    ``key_claims`` when it has them, else its grounded ``script_body`` sentences.

    A KEYLESS corpus (e.g. London — every beat has ``key_claims=()``) would otherwise
    make ``claims_realized_by`` return an empty baseline, turning the coverage
    (deletion) gate into a NO-OP — so a bold fusion could silently drop a fact there.
    Falling back to the body sentences gives keyless corpora a real deletion gate.
    Keyed corpora (Paris/NYC) are unchanged (their key_claims take precedence).
    """
    if beat.key_claims:
        return beat.key_claims
    if not beat.script_body:
        return ()
    from .generation import split_sentences  # deferred: generation imports this module

    # Pseudo-claim = each FULL body sentence. A round-4 attempt to key only on "hard
    # facts" (years + proper nouns) was REVERTED: on the live London corpus it left
    # 85/562 (15.1%) keyless beats with a <2-token baseline — e.g. "…a place for duels
    # …members of the nobility" collapses to "The" — turning the deletion gate back into
    # a NO-OP for those (the exact silent-fact-loss this subsystem exists to prevent).
    # The full sentence keeps every fact gated. Over-gating a bold paraphrase is the
    # tolerable failure direction: _prefer_deduped fail-opens to the complete stitched
    # original — no fact lost, only a less-fused telling.
    return tuple(s for s in (p.strip() for p in split_sentences(beat.script_body)) if s)


def claims_realized_by(
    script: Script, beats_by_id: dict[str, BeatRef]
) -> set[tuple[str, int]]:
    """The ``(beat_id, claim_index)`` pairs realized by ≥1 beat-cited sentence.

    "Realized" = some beat sentence's signature overlaps the claim's at
    ``COVERAGE_MATCH_MIN`` (lenient — coverage guards only gross deletion; the
    faithfulness gate guards quiet loss). Run on the PRE-compose stitch to get the
    coverage baseline (what actually got voiced — not every ``key_claim``, since a
    beat's prose may never voice some of its claims), and on the composed output
    to prove nothing the stitch voiced was lost. Order-independent (a set).
    """
    sent_sigs = [
        sig
        for s in script.script
        if s.source_type == "beat" and (sig := _signature(s.text))
    ]
    realized: set[tuple[str, int]] = set()
    for bid, beat in beats_by_id.items():
        for ci, claim in enumerate(_claims_for_coverage(beat)):
            csig = _signature(claim)
            if len(csig) < MIN_SHARED_TOKENS:
                continue
            if any(_overlap(csig, ssig) >= COVERAGE_MATCH_MIN for ssig in sent_sigs):
                realized.add((bid, ci))
    return realized


def verify_claim_coverage(
    composed: Script,
    expected: set[tuple[str, int]],
    beats_by_id: dict[str, BeatRef],
) -> tuple[tuple[str, str], ...]:
    """Compose may MERGE or reword a fact, but never silently DELETE one.

    Returns ``(beat_id, claim_text)`` for every claim the pre-compose stitch
    voiced (``expected``) that NO composed sentence still realizes — the deletion
    blind spot the invention-focused checks (traceability / provenance /
    entailment) all share. Deduped paraphrase twins are handled for free: a
    twin's realizing sentence keeps the shared fact covered, so folding two
    beats' paraphrases into one telling passes, while dropping a distinct fact
    fails.
    """
    still = claims_realized_by(composed, beats_by_id)
    out: list[tuple[str, str]] = []
    for bid, ci in sorted(expected - still):
        beat = beats_by_id.get(bid)
        claims = _claims_for_coverage(beat) if beat else ()
        claim = claims[ci] if ci < len(claims) else ""
        out.append((bid, claim))
    return tuple(out)


def candidate_duplicate_pairs(
    script: Script, *, min_overlap: float = 0.3, max_pairs: int = 30
) -> tuple[tuple[int, str, str], ...]:
    """High-RECALL pairs of same-stop beat sentences whose content signatures
    overlap at ``min_overlap`` — candidates the compose LLM is TOLD may restate
    one fact, so IT (not this threshold) makes the fuse/keep call on each.

    Deliberately loose and hint-only: the red-team proved no threshold safely
    DROPS at these overlaps, but a hint drops nothing, so recall beats precision.
    Semantic repeats with near-zero lexical overlap (different framings of one
    fact) are missed here on purpose — the compose prompt's bold-fusion mandate
    covers those. Returns up to ``max_pairs`` ``(stop_idx, text_a, text_b)``.
    """
    by_stop: dict[int, list[Sentence]] = defaultdict(list)
    for s in script.script:
        if s.source_type == "beat":
            by_stop[s.stop_idx].append(s)
    pairs: list[tuple[int, str, str]] = []
    for stop_idx in sorted(by_stop):
        sents = by_stop[stop_idx]
        sigs = [_signature(s.text) for s in sents]
        for i in range(len(sents)):
            for j in range(i + 1, len(sents)):
                if (
                    len(sigs[i] & sigs[j]) >= MIN_SHARED_TOKENS
                    and _overlap(sigs[i], sigs[j]) >= min_overlap
                ):
                    pairs.append((stop_idx, sents[i].text, sents[j].text))
                    if len(pairs) >= max_pairs:
                        return tuple(pairs)
    return tuple(pairs)


__all__ = [
    "candidate_duplicate_pairs",
    "claims_realized_by",
    "suppress_exact_repeats",
    "suppress_repeated_claims",
    "verify_claim_coverage",
]
