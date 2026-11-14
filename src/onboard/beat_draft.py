"""Beat DRAFT: turn each POI's pinned Wikipedia lead into SEVERAL beats.

A POI must carry **≥ 3 active beats** (and be tier ≥ 3) to count as an *anchor
candidate* for the tour engine's density gate (``src/tour/density.py`` —
``ANCHOR_CANDIDATE_BEAT_COUNT_MIN = 3``). A city drafted with one beat per POI
therefore yields ZERO anchors and the engine refuses (RED) at every duration.
So each POI's lead is split into 3-5 contiguous, VERBATIM sentence-spans and one
beat is emitted per span — enough active beats for a landmark POI to anchor.

Two drafters, one wall between free and paid:

- ``MockBeatDrafter`` is free and deterministic. Each beat's ``source_passage``
  is a VERBATIM contiguous span of ``extract.text`` (a literal slice, never a
  paraphrase), so every emitted beat is a normalized substring of the pinned
  ``wikipedia/{poi_slug}-rev-{revid}.txt`` file ``write_city`` wrote from the
  SAME text — i.e. it passes ``scripts/validate_beats.validate`` unchanged. Its
  ``script_body`` is the span's first sentence (a real, verbatim sentence — never
  a synthesized ``"POI: fragment."`` stub).
- ``AnthropicBeatDrafter`` spends real API credits, so it can NEVER run without
  an explicit ``confirm=True``: ``draft_all`` raises ``CostNotConfirmed`` (which
  carries the dollar estimate for the FULL beat count, not the POI count) BEFORE
  any client is constructed or any call is made. Its ``source_passage`` is ALSO
  the mechanical verbatim slice — only the narration prose (``script_body``)
  comes from the model, and only from the given span (never WebFetch/memory) —
  so a paid beat can no more be grounded in reconstructed memory than a mock one.

Every beat mirrors a real ``data/*/beats.json`` wikipedia beat and is built from
the FROZEN ``WikiExtract`` and the shared ``slugify`` / ``hash_body`` helpers in
``scripts/beat_builder.py`` (imported, never re-implemented). The beats of one
POI share a CITY-PREFIXED ``beat_id`` stem and a taggable (non-parent) ``lens``
but carry a per-span ``topic_slug`` and a distinct verbatim body, so their
identity tuples and body hashes are unique (``validate_beats`` passes). No
``fact_check`` block: an absent status is allowed by validate, whereas a
"verified" status would (rightly) trip the verification-freshness gate.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any

from scripts.beat_builder import hash_body, slugify
from src.onboard.assemble import WikiExtract

MODEL = "claude-opus-4-8"

# Provider selection. There is NO default — get_drafter() fails closed on an
# unset or unknown value. "anthropic" opts into paid drafting (still gated
# behind confirm=True in draft_all); "mock" must be selected EXPLICITLY.
ONBOARD_PROVIDER = os.getenv("ONBOARD_PROVIDER")

# Cost model for claude-opus-4-8: $5 / 1M input tokens, $25 / 1M output tokens.
# Per BEAT (a POI now yields ~3 beats), so draft_all's confirm gate estimates the
# full drafted-beat count, not the POI count.
_INPUT_TOKENS_PER_BEAT = 1200
_OUTPUT_TOKENS_PER_BEAT = 350
_INPUT_USD_PER_TOKEN = 5.0 / 1e6
_OUTPUT_USD_PER_TOKEN = 25.0 / 1e6

# A landmark POI needs ≥ this many beats to clear the tour engine's anchor gate
# (density.ANCHOR_CANDIDATE_BEAT_COUNT_MIN); we never emit fewer while the lead
# has the sentences for it. Capped so a very long lead doesn't explode.
_TARGET_SPANS = 3
_MAX_SPANS = 5

# The Wikipedia-lead beats are tagged with a TAGGABLE CHILD lens (never a parent
# genre like "history", which is is_parent:True and absent from TAGGABLE_LENSES —
# tagging to it would tag a parent Lens node and trip
# test_no_parent_lens_in_city_beats). "historic_arch" is the most common lens on
# real establishing Wikipedia beats in data/new_york/beats.json. Membership in
# TAGGABLE_LENSES is guarded by tests/test_onboard_beat_draft.py.
_WIKIPEDIA_LENS = "historic_arch"

_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


# ---------------------------------------------------------------------------
# Verbatim-span helpers (shared by both drafters).
# ---------------------------------------------------------------------------
def _sentence_bounds(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Return ``(stripped_text, [(start, end), ...])`` — the char offsets of each
    sentence in ``text``. ``stripped_text[start:end]`` is a VERBATIM sentence
    (terminal punctuation included, trailing whitespace excluded), so any group
    of consecutive sentences sliced back out of ``stripped_text`` is a literal
    contiguous substring — never a re-joined approximation."""
    s = text.strip()
    bounds: list[tuple[int, int]] = []
    start = 0
    for m in _SENTENCE_END_RE.finditer(s):
        bounds.append((start, m.start()))
        start = m.end()
    if start < len(s):
        bounds.append((start, len(s)))
    return s, bounds


def _num_spans(n_sentences: int) -> int:
    """How many spans (beats) to carve from a lead of ``n_sentences``.

    Aims for ~2 sentences per span so at least one span has ≥ 2 sentences (which
    the grounding/paraphrase gate needs), while guaranteeing ≥ 3 spans whenever
    the lead has ≥ 3 sentences (so a landmark POI can anchor) and capping at 5.
    """
    if n_sentences <= 0:
        return 0
    n = min(_MAX_SPANS, max(n_sentences // 2, 1))
    return max(n, min(_TARGET_SPANS, n_sentences))


def _span_texts(text: str) -> list[str]:
    """Split ``text`` into contiguous, VERBATIM sentence-spans (one per beat).

    Contiguous groups of whole sentences, front-loaded so the first
    (establishing) span carries the extra sentence when the split is uneven — so
    the establishing span is the richest and is ≥ 2 sentences for a ≥ 4-sentence
    lead. Each returned string is a literal substring of ``text.strip()``.
    """
    s, bounds = _sentence_bounds(text)
    k = len(bounds)
    n = _num_spans(k)
    if n == 0:
        return []
    if n == 1:
        return [s[bounds[0][0] : bounds[-1][1]]] if bounds else [s]
    base, rem = divmod(k, n)
    spans: list[str] = []
    idx = 0
    for j in range(n):
        take = base + (1 if j < rem else 0)
        start_char = bounds[idx][0]
        end_char = bounds[idx + take - 1][1]
        spans.append(s[start_char:end_char])
        idx += take
    return spans


def _first_sentence(span: str) -> str:
    """The span's first sentence, VERBATIM — a real, self-contained sentence
    (used as the mock ``script_body`` and the paid-path fallback). This is a
    literal substring of the span, so it is grounded and is never a synthesized
    ``"POI: fragment."`` stub."""
    parts = _SENTENCE_END_RE.split(span.strip(), maxsplit=1)
    return parts[0].strip()


def _paraphrase(span: str) -> str:
    """Simulate a drafter that RECONSTRUCTS from memory instead of quoting: a
    fabricated lead-in on every sentence so the passage is no longer a verbatim
    substring of the pinned file. Used only by the mock's paraphrase mode (the
    undo test); the grounding gate needs ≥2 ungrounded fragments to fire, so a
    single-word edit could not exercise it — the whole passage must drift, which
    is why the establishing span carries ≥ 2 sentences."""
    parts = [p for p in _SENTENCE_END_RE.split(span.strip()) if p]
    return " ".join(f"Reportedly, {p}" for p in parts)


def _arc(i: int) -> tuple[str, str]:
    """The (narrative_function, beat_type) for the i-th span of a POI. The first
    span ESTABLISHES; the rest DEEPEN — mirroring the real corpus shape (1
    establishing + N deepen per POI) so the beats read as a small arc and count
    as distinct active beats for the anchor gate."""
    if i == 0:
        return "establishing", "establishing"
    return "deepen", "factoid"


def _build_beat(
    *,
    city_slug: str,
    poi_name: str,
    extract: WikiExtract,
    index: int,
    narrative_function: str,
    beat_type: str,
    script_body: str,
    source_passage: str,
) -> dict:
    """Assemble one wikipedia beat matching a real ``beats.json`` row and accepted
    unchanged by ``scripts/validate_beats.validate``.

    ``beat_id`` and ``city_name`` are CITY-PREFIXED so this POI's beats can never
    collide with paris/new_york or another onboarded city. Within a POI the
    per-span ``index`` keeps ``topic_slug`` (and thus the identity tuple) and
    ``beat_id`` unique; distinct spans give distinct ``script_body_hash``es.
    ``book_slug="wikipedia"`` arms the grounding gate; ``source_chunk_slug`` is
    the pinned filename stem ``write_city`` produces. No ``fact_check`` block
    (absent status is allowed).
    """
    poi_slug = slugify(poi_name)
    body = script_body.strip()
    return {
        "beat_id": f"{city_slug}_{poi_slug}_wikipedia_{index}",
        "city_name": city_slug,
        "poi_name": poi_name,
        "parent_poi": None,
        "lens": _WIKIPEDIA_LENS,
        "topic_slug": f"{poi_slug}_lead_{index}",
        "book_slug": "wikipedia",
        "source_chunk_slug": f"{poi_slug}-rev-{extract.revid}",
        "script_body": body,
        "script_body_hash": hash_body(body),
        "beat_length_class": "mid",
        "duration_sec": 30,
        "kid_friendly": "yes",
        "entities": [],
        "physical_cues": [],
        "inline_foreign_phrases": [],
        "key_claims": [],
        "sensory_anchor": False,
        "narrative_function": narrative_function,
        "beat_type": beat_type,
        "emotional_register": "neutral",
        "source_passage": source_passage.strip(),
        "source_attribution": {
            "source_type": "wikipedia",
            "book_title": "Wikipedia",
            "article_title": extract.article_title,
            "url": extract.url,
            "revision_id": extract.revid,
            "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "section": "Lead",
        },
    }


# ---------------------------------------------------------------------------
# Drafters. Each ``draft`` returns the LIST of beats for one POI.
# ---------------------------------------------------------------------------
class MockBeatDrafter:
    """Free, deterministic drafter. Each beat's ``source_passage`` is a verbatim
    span of the pinned text; its ``script_body`` is the span's first sentence.
    With ``paraphrase=True`` the ``source_passage`` is instead a memory-style
    paraphrase (used only to prove the grounding gate bites)."""

    def __init__(self, *, paraphrase: bool = False) -> None:
        self._paraphrase = paraphrase

    def draft(self, poi_name: str, extract: WikiExtract, city_slug: str) -> list[dict]:
        beats: list[dict] = []
        for i, span in enumerate(_span_texts(extract.text)):
            narrative_function, beat_type = _arc(i)
            source_passage = _paraphrase(span) if self._paraphrase else span
            beats.append(
                _build_beat(
                    city_slug=city_slug,
                    poi_name=poi_name,
                    extract=extract,
                    index=i,
                    narrative_function=narrative_function,
                    beat_type=beat_type,
                    script_body=_first_sentence(span),
                    source_passage=source_passage,
                )
            )
        return beats


class AnthropicBeatDrafter:
    """Paid drafter. The model writes only ``script_body`` and only from the given
    span; ``source_passage`` remains the mechanical verbatim slice, so a paid beat
    cannot be grounded in reconstructed memory. The client is constructed lazily
    so ``draft_all(confirm=False)`` never builds one."""

    def __init__(self, *, client: Any = None) -> None:
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            # Bounded, like every tour-engine client (src/tour/anthropic_client.py):
            # a bare anthropic.Anthropic() inherits the SDK's 600 s x 2-retry default,
            # i.e. up to 30 minutes on one stalled call. Onboarding drafts beats in
            # bulk, so an unbounded stall silently parks a whole city's run.
            # Imported lazily — never at draft_all's confirm gate.
            from src.tour.anthropic_client import compose_client

            self._client = compose_client()
        return self._client

    def draft(self, poi_name: str, extract: WikiExtract, city_slug: str) -> list[dict]:
        beats: list[dict] = []
        for i, span in enumerate(_span_texts(extract.text)):
            narrative_function, beat_type = _arc(i)
            message = self._get_client().messages.create(
                model=MODEL,
                max_tokens=512,
                messages=[{"role": "user", "content": _draft_prompt(poi_name, span)}],
            )
            script_body = _response_text(message) or _first_sentence(span)
            beats.append(
                _build_beat(
                    city_slug=city_slug,
                    poi_name=poi_name,
                    extract=extract,
                    index=i,
                    narrative_function=narrative_function,
                    beat_type=beat_type,
                    script_body=script_body,
                    source_passage=span,
                )
            )
        return beats


def _draft_prompt(poi_name: str, source_span: str) -> str:
    return (
        "Write one narration passage of about 60 to 70 words for a stop on an "
        f"audio walking tour at {poi_name}. Ground every statement ONLY in the "
        "source text below — do not add any fact, name, date, or figure that is "
        "not present in it, and do not draw on outside memory. Write flowing "
        "spoken prose (no lists, no headings).\n\nSOURCE:\n" + source_span
    )


def _response_text(message: Any) -> str:
    blocks = getattr(message, "content", None) or []
    return " ".join(getattr(b, "text", "") for b in blocks if getattr(b, "text", "")).strip()


_DRAFTERS: dict[str, type] = {
    "mock": MockBeatDrafter,
    "anthropic": AnthropicBeatDrafter,
}


def get_drafter(name: str | None = None) -> MockBeatDrafter | AnthropicBeatDrafter:
    """Return the drafter by name, else by the ONBOARD_PROVIDER pin.

    FAILS CLOSED — there is deliberately no default.

    This used to read ``os.getenv("ONBOARD_PROVIDER", ONBOARD_PROVIDER)`` and
    return ``MockBeatDrafter()`` for an unset value AND for every unrecognised
    one. ``scripts/workbench.sh`` pins no drafter, so the workbench's Draft Beats
    button — a workbench-only route (``src/api/app.py`` mounts it only under
    ``WORKBENCH_API_ENABLED``) — drafted every beat with the fake and showed a
    human the result as drafted content. A typo in the pin did the same thing
    silently. That is the editorial twin of the silent-WAV TTS default, and the
    same fix applies: a misconfigured process must say so loudly rather than look
    fine and be fake.

    ``"mock"`` stays SELECTABLE and free, because callers opt into it explicitly
    and must keep working at $0 — ``Makefile``'s ``onboard-city`` passes
    ``ONBOARD_PROVIDER`` (now REQUIRED — no default), ``tests/conftest.py`` sets
    it, and the CLI documents the free path. What is gone is the silent fallback
    a caller got without asking for it.

    Mirrors ``src/audio/provider.py``'s ``get_provider()`` exactly, including the
    optional ``name`` argument. That argument is not decoration: the registry
    audit in ``tests/test_workbench_matches_the_app.py`` derives a registry's
    resolver structurally as "a module-level function that reads the registry,
    TAKES AN ARGUMENT, and returns a constructed object", then probes a real
    server process to confirm each key resolves to the class the source maps it
    to. A resolver that only reads the environment is invisible to that audit, so
    the registry would look dead and the audit would silently cover nothing.
    """
    name = name or os.getenv("ONBOARD_PROVIDER")
    available = ", ".join(sorted(_DRAFTERS))
    if not name:
        raise ValueError(
            "No beat drafter selected: set ONBOARD_PROVIDER. "
            f"Available: {available}. (There is no default — silently drafting "
            f"with the free mock is exactly the defect this fail-closed check "
            f"replaces.)"
        )
    cls = _DRAFTERS.get(name)
    if cls is None:
        raise ValueError(f"Unknown beat drafter {name!r}. Available: {available}")
    return cls()


# ---------------------------------------------------------------------------
# Cost estimate + confirmation gate.
# ---------------------------------------------------------------------------
def planned_beat_count(pois: list[dict], extracts_by_slug: dict[str, WikiExtract]) -> int:
    """How many beats ``draft_all`` WOULD draft — the sum of span counts over the
    POIs that have a matching extract. Pure sentence-splitting, no API call, so
    the confirm gate can estimate the true (multi-beat) cost before drafting."""
    total = 0
    for poi in pois:
        extract = extracts_by_slug.get(slugify(poi["name"]))
        if extract is None:
            continue
        total += len(_span_texts(extract.text))
    return total


def estimate_cost(n: int) -> dict:
    """Dollar estimate for drafting ``n`` beats on claude-opus-4-8, assuming
    ~1200 input + ~350 output tokens per beat. ``n`` is a BEAT count (a POI now
    yields several beats)."""
    est_usd = n * (
        _INPUT_TOKENS_PER_BEAT * _INPUT_USD_PER_TOKEN
        + _OUTPUT_TOKENS_PER_BEAT * _OUTPUT_USD_PER_TOKEN
    )
    return {
        "model": MODEL,
        "beats": n,
        "est_input_tokens": n * _INPUT_TOKENS_PER_BEAT,
        "est_output_tokens": n * _OUTPUT_TOKENS_PER_BEAT,
        "est_usd": round(est_usd, 4),
    }


class CostNotConfirmed(Exception):  # noqa: N818 — public name is the caller/test contract
    """Raised when a paid (anthropic) draft run is requested without
    ``confirm=True``. Carries the cost ``estimate`` so the caller can show it and
    re-invoke with ``confirm=True``."""

    def __init__(self, estimate: dict) -> None:
        self.estimate = estimate
        super().__init__(
            f"anthropic beat drafting for {estimate['beats']} beat(s) would cost "
            f"~${estimate['est_usd']} — pass confirm=True to proceed."
        )


def draft_all(
    pois: list[dict],
    extracts_by_slug: dict[str, WikiExtract],
    *,
    confirm: bool = False,
    drafter: MockBeatDrafter | AnthropicBeatDrafter | None = None,
) -> list[dict]:
    """Draft ~3-5 beats per POI that has a matching extract (by
    ``slugify(poi_name)``), splitting the pinned lead into verbatim spans. The
    mock is free; a paid ``AnthropicBeatDrafter`` with ``confirm=False`` raises
    ``CostNotConfirmed`` — carrying the estimate for the FULL beat count — BEFORE
    any client is built or any call is made. The city prefix for each beat comes
    from the POI's own ``city_name`` (stamped by ``assemble``).

    DEDUP (the corpus-wide content wall): a denser city inevitably produces beats
    that share IDENTICAL content across POIs — two POIs resolving to the SAME
    Wikipedia article (a geo-split "(2)" twin) yield identical extracts → identical
    spans → identical bodies, and two DIFFERENT POIs' leads can share an identical
    sentence → identical ``script_body``. Either way ``validate_beats`` rightly
    HARD-FAILS with ``HASH_COLLISION`` (``script_body_hash`` must be unique file-wide).
    So we dedup ACROSS THE WHOLE RUN: a beat whose ``script_body_hash`` OR whose
    verbatim ``source_passage`` span has already been emitted is SKIPPED — never a
    second copy. The FIRST occurrence (in POI/span order) survives, preserving its
    grounding + per-span ``index`` (so surviving ``beat_id``/``topic_slug`` stay
    unique and the beats still read in order). A POI whose beats ALL collide with
    earlier ones (a pure duplicate) ends up with 0 beats — an acceptable beatless
    nav-POI (validate checks beat→POI, not POI→beat)."""
    drafter = drafter or get_drafter()
    if isinstance(drafter, AnthropicBeatDrafter) and not confirm:
        raise CostNotConfirmed(estimate_cost(planned_beat_count(pois, extracts_by_slug)))
    beats: list[dict] = []
    seen_hashes: set[str] = set()
    seen_passages: set[str] = set()
    for poi in pois:
        extract = extracts_by_slug.get(slugify(poi["name"]))
        if extract is None:
            continue
        city_slug = poi.get("city_name") or ""
        for beat in drafter.draft(poi["name"], extract, city_slug):
            body_hash = beat["script_body_hash"]
            passage = beat["source_passage"]
            if body_hash in seen_hashes or passage in seen_passages:
                continue  # identical content already emitted — skip the duplicate
            seen_hashes.add(body_hash)
            seen_passages.add(passage)
            beats.append(beat)
    return beats
