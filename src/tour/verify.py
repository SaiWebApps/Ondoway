"""VERIFY with teeth — provenance + faithfulness checks.

Two INDEPENDENT checks layered on top of validation.validate_script's
traceability + forbidden-phrase gates (rapidfuzz alone is not faithfulness):

1. **Provenance** (deterministic, ``rapidfuzz``): each beat's verbatim
   ``source_passage`` must fuzzy-match the source chunk it was extracted
   from, above ``PROVENANCE_MATCH_THRESHOLD``. Catches a beat whose stored
   passage drifted from (or never existed in) its cited chunk.

2. **Faithfulness** (one entailment call per stop): every beat-cited
   sentence must follow from that beat's ``key_claims``. Pluggable
   ``FaithfulnessChecker`` — ``MockFaithfulnessChecker`` (default, offline,
   trusts the corpus) keeps ``make test`` free; ``HaikuFaithfulnessChecker``
   runs the real entailment in dev/CI/prod.

Both write to ``ValidationReport`` (the Script is never mutated). A beat
that lacks ``source_passage``/``key_claims`` is SKIPPED, not failed — the
corpus extraction pipeline backfills these; until then these checks are
no-ops and the traceability/forbidden gates still apply.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Protocol

from rapidfuzz import fuzz

from .contract import BeatRef, BeatSequence, Script, Sentence
from .generation import GLUE_CLOSING, GLUE_REFLECTION, is_fallback_close, split_sentences

# A verbatim passage present in its chunk scores ~100 (partial_ratio is
# substring-tolerant); 88 leaves headroom for whitespace/punctuation drift
# while still catching a fabricated or wrong-chunk passage (which scores low).
PROVENANCE_MATCH_THRESHOLD: float = 88.0


def verify_provenance(
    beat_sequence: BeatSequence,
    chunk_text_by_slug: dict[str, str],
    *,
    threshold: float = PROVENANCE_MATCH_THRESHOLD,
) -> list[tuple[str, float]]:
    """(beat_id, score) for every provenanced beat that fails its chunk match.

    Beats with no ``source_passage``/``source_chunk_slug`` are skipped. A beat
    citing a slug absent from ``chunk_text_by_slug`` fails at score 0.0 — it
    claims provenance we cannot confirm.
    """
    failures: list[tuple[str, float]] = []
    for plan in beat_sequence.poi_beats:
        for beat in plan.beats:
            if not beat.source_passage or not beat.source_chunk_slug:
                continue
            chunk = chunk_text_by_slug.get(beat.source_chunk_slug)
            if not chunk:
                failures.append((beat.id, 0.0))
                continue
            score = fuzz.partial_ratio(beat.source_passage, chunk)
            if score < threshold:
                failures.append((beat.id, score))
    return failures


class FaithfulnessChecker(Protocol):
    """Does ``sentence_text`` follow from ``key_claims``? (entailment)"""

    def entails(self, key_claims: tuple[str, ...], sentence_text: str) -> bool: ...


class MockFaithfulnessChecker:
    """Offline default: trusts the corpus (every beat-cited sentence entails).

    Tests inject a stub to exercise failures; production wires the Haiku
    checker. Records calls so the gate's call-count can be asserted.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str]] = []

    def entails(self, key_claims: tuple[str, ...], sentence_text: str) -> bool:
        self.calls.append((key_claims, sentence_text))
        return True


FAITHFULNESS_MODEL = "claude-haiku-4-5-20251001"
# Concurrency for the per-sentence entailment checks (independent Haiku calls).
_FAITHFULNESS_MAX_WORKERS = 8
_ENTAILMENT_PROMPT = (
    "You are a strict fact-checker. Given a list of KEY CLAIMS and one "
    "SENTENCE, answer with exactly 'YES' if the sentence is fully supported "
    "by the claims, or 'NO' if it adds, contradicts, or overstates anything.\n\n"
    "KEY CLAIMS:\n{claims}\n\nSENTENCE:\n{sentence}\n\nAnswer (YES or NO):"
)


@dataclass
class HaikuFaithfulnessChecker:
    """Real one-call-per-sentence entailment via Anthropic Haiku (dev/CI/prod).

    Mirrors HaikuGlueClient: defers the anthropic import so unit tests never
    need the SDK, and only runs when wired in explicitly (Mock is the
    default everywhere else). NOT exercised by ``make test`` — its live
    behavior needs ANTHROPIC_API_KEY (set on the Render service).
    """

    model: str = FAITHFULNESS_MODEL
    max_output_tokens: int = 5
    calls: int = 0
    _client: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        from src.tour.anthropic_client import judge_client

        self._client = judge_client()

    def entails(self, key_claims: tuple[str, ...], sentence_text: str) -> bool:
        self.calls += 1
        rendered = _ENTAILMENT_PROMPT.format(
            claims="\n".join(f"- {c}" for c in key_claims), sentence=sentence_text
        )
        response = self._client.messages.create(  # type: ignore[attr-defined]
            model=self.model,
            max_tokens=self.max_output_tokens,
            temperature=0,  # deterministic entailment: a borderline sentence must
            # give the SAME verdict every verify, or the gate flakes and a compose
            # that just passed fails on the post-repair re-verify (never converges).
            messages=[{"role": "user", "content": rendered}],
        )
        text = "".join(
            getattr(b, "text", "") for b in (getattr(response, "content", []) or [])
        ).strip().upper()
        # Conservative: only an explicit YES passes; anything else fails closed.
        return text.startswith("YES")


def _visited_claims(
    script: Script,
    beats_by_id: dict[str, BeatRef],
    before_stop_idx: int,
) -> tuple[str, ...]:
    """Order-preserving, deduped union of key_claims of beats cited at
    stop_idx STRICTLY below ``before_stop_idx`` — what the walker has already
    heard when a reflection on the leg into that stop is spoken."""
    seen_claims: set[str] = set()
    seen_beats: set[str] = set()
    out: list[str] = []
    for s in script.script:
        if s.source_type != "beat" or s.stop_idx >= before_stop_idx:
            continue
        if s.source_id in seen_beats:
            continue
        seen_beats.add(s.source_id)
        beat = beats_by_id.get(s.source_id)
        if beat is None:
            continue
        for claim in beat.key_claims:
            if claim not in seen_claims:
                seen_claims.add(claim)
                out.append(claim)
    return tuple(out)


def _own_stop_support(
    script: Script, beats_by_id: dict[str, BeatRef], stop_idx: int
) -> tuple[str, ...]:
    """The claims AND bodies of every beat cited AT ``stop_idx`` — what a close may land.
    The reflection's `_visited_claims` looks strictly BEFORE a stop; a close looks
    at its own."""
    cited: list[str] = []
    for sentence in script.script:
        if sentence.stop_idx != stop_idx or sentence.source_type != "beat":
            continue
        for bid in sentence.cited_beat_ids:
            if bid not in cited:
                cited.append(bid)
    support: list[str] = []
    for bid in cited:
        beat = beats_by_id.get(bid)
        if beat is None:
            continue
        support.extend(beat.key_claims)
        if beat.script_body:
            support.append(beat.script_body)
    return tuple(support)


def _normalize_for_verbatim(text: str) -> str:
    """Whitespace/case-fold so 'appears verbatim in the source' is robust to
    line wrapping and trivial spacing differences."""
    return " ".join(text.split()).casefold()


def verify_faithfulness(
    script: Script,
    beats_by_id: dict[str, BeatRef],
    checker: FaithfulnessChecker,
) -> list[tuple[Sentence, str]]:
    """(sentence, reason) for each beat-cited sentence not entailed by its
    beat. Sentences whose beat has no ``key_claims`` are skipped (nothing to
    entail against yet).

    The corpus is CANONICAL (same principle as validation.py's glue-only
    scan): a beat-cited sentence found verbatim in its beat's script_body is
    trivially faithful — no entailment call. Only LLM-REWRITTEN sentences are
    checked, and against the beat's key_claims PLUS its script_body — the
    claims are a summary subset, and the first live gate run (2026-07-02)
    showed claims-only entailment flags the corpus's own detail sentences as
    unfaithful. Invented facts still fail: they are in neither.

    Reflections are FAIL-CLOSED, unlike other glue: a
    ``GLUE_REFLECTION`` sentence must entail from the union of key_claims of
    beats cited STRICTLY BEFORE its stop (what the walker has already heard);
    an empty union is itself a failure — an unverifiable reflection never
    ships. Reflections stay claims-only: they are synthesis, not retelling.
    """
    # Collect, in script order, each check as (sentence, support, fail_reason).
    # support=None marks an IMMEDIATE failure (no LLM call needed).
    checks: list[tuple[Sentence, tuple[str, ...] | None, str]] = []
    for sentence in script.script:
        if sentence.source_type == "glue" and sentence.source_id == GLUE_REFLECTION:
            # The slot holds THE THREAD — one sentence binding THIS stop to the
            # walk through ONE fact of this stop, never a recap. Its support is what
            # the walker has heard PLUS the arriving stop's own beats: the old
            # visited-only window was the recap's, and it ruled the thread's
            # defining fact inadmissible by construction. Still fail-closed on an
            # empty union: an unverifiable line never ships.
            claims = _visited_claims(script, beats_by_id, sentence.stop_idx)
            own = _own_stop_support(script, beats_by_id, sentence.stop_idx)
            support = (*claims, *(piece for piece in own if piece not in claims))
            if not support:
                checks.append((sentence, None, "unverifiable_reflection:no_support"))
            else:
                checks.append((sentence, support, "unfaithful_reflection"))
            continue
        if sentence.source_type == "glue" and sentence.source_id == GLUE_CLOSING:
            # An AUTHORED close lands only what ITS stop voiced — entailed against
            # the claims and bodies of the beats cited at its own stop, the
            # reflection's rule turned inward. The stitch's fallback template
            # carries no fact and is exempt (never sent to the checker).
            poi_name = (
                script.selected_pois[sentence.stop_idx].name
                if sentence.stop_idx < len(script.selected_pois)
                else ""
            )
            if is_fallback_close(
                sentence.text,
                script.inputs,
                poi_name=poi_name,
                n_stops=len(script.selected_pois),
            ):
                continue
            support = _own_stop_support(script, beats_by_id, sentence.stop_idx)
            if support:
                checks.append((sentence, support, "unfaithful_close"))
            continue
        if sentence.source_type != "beat":
            continue
        # Multi-beat citation: a FUSED sentence draws on source_id + also_cites;
        # entail it against the UNION of those beats' key_claims + bodies, so a
        # cross-book merge ("in 1800 [beat A] Napoleon promised naming rights
        # [beat B]") is faithful instead of failing on the primary beat alone.
        cited = [beats_by_id[bid] for bid in sentence.cited_beat_ids if bid in beats_by_id]
        if not any(b.key_claims or b.script_body for b in cited):
            continue  # nothing to entail against (support = key_claims + script_body
            # below), so a KEYLESS corpus (e.g. London, key_claims=[]) is still gated
            # against its grounded script_body instead of skipping faithfulness.
        if passes_sentence_unit_shortcut(sentence.text, cited):
            continue  # canonical corpus text, unchanged — trivially faithful.
            # WHOLE sentence-units only (never plain substring): a hedge-strip
            # ("According to lore, X" -> "X") or a negation-truncation ("never
            # named it after himself" -> "named it after himself") is a substring
            # of the body but flips its meaning — those must be entailment-checked.
        support = tuple(
            piece
            for b in cited
            for piece in (*b.key_claims, *((b.script_body,) if b.script_body else ()))
        )
        checks.append((sentence, support, f"unfaithful:{sentence.source_id}"))

    # The entailment checks are independent one-call-per-sentence Haiku calls and,
    # run sequentially, are the dominant compose-gate latency (hundreds of calls,
    # ~15 min on a live tour). Run them CONCURRENTLY; assembly stays in script
    # order so the failure list is byte-identical to the sequential version.
    entail_positions = [i for i, (_, sup, _) in enumerate(checks) if sup is not None]
    verdicts: dict[int, bool] = {}
    if entail_positions:
        workers = min(_FAITHFULNESS_MAX_WORKERS, len(entail_positions))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(checker.entails, checks[i][1], checks[i][0].text): i
                for i in entail_positions
            }
            for future, i in futures.items():
                verdicts[i] = future.result()

    failures: list[tuple[Sentence, str]] = []
    for i, (sentence, support, reason) in enumerate(checks):
        if support is None or not verdicts.get(i, True):
            failures.append((sentence, reason))
    return failures


def passes_sentence_unit_shortcut(text: str, cited: list[BeatRef]) -> bool:
    """True iff the normalized sentence equals ONE complete sentence-unit, or a
    CONTIGUOUS run of complete units, of a cited beat's ``script_body``
    (units per ``generation.split_sentences``).

    Ported from the correct-don't-reject branch, where it replaced a plain
    substring check: a FRAGMENT of a corpus sentence — a negation-truncation
    ("[never] named his hotel...") or an attribution-strip ("[According to lore,]
    Orwell worked here") — IS a substring but is not a complete unit run, so the
    substring check let it ship unverified. Requiring whole units closes that.
    """
    norm = _normalize_for_verbatim(text)
    if not norm:
        return False
    for beat in cited:
        if not beat.script_body:
            continue
        units = [_normalize_for_verbatim(u) for u in split_sentences(beat.script_body)]
        for i in range(len(units)):
            acc = units[i]
            if acc == norm:
                return True
            for j in range(i + 1, len(units)):
                acc = f"{acc} {units[j]}"
                if acc == norm:
                    return True
                if len(acc) > len(norm):
                    break
    return False
