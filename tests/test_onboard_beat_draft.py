"""Unit tests for src/onboard/beat_draft.py (the beat-drafting step of new-city onboarding).

Pure — no Neo4j, no network, $0. Run with:
    make test-file FILE=tests/test_onboard_beat_draft.py

The load-bearing guarantees:

- Each POI now yields **≥ 3 beats** (one per verbatim sentence-span of its pinned
  Wikipedia lead), so a landmark POI carries enough active beats to clear the
  tour engine's anchor gate (``density.ANCHOR_CANDIDATE_BEAT_COUNT_MIN = 3``).
  With one beat per POI the engine finds zero anchors and refuses (RED).
- Every beat is byte-for-byte acceptable to the same commit-time gate a real
  Wikipedia beat must pass (``scripts/validate_beats.validate``): its
  ``source_passage`` is a VERBATIM slice of the pinned revision text (grounded),
  its ``lens`` is a TAGGABLE child (never a parent genre), its ``beat_id`` is
  CITY-PREFIXED, and its identity tuple + body hash are unique per span.
- The paid provider cannot spend a cent without an explicit ``confirm=True``, and
  its estimate reflects the FULL (multi-beat) count, not the POI count.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import validate_beats
from scripts.beat_builder import hash_body, slugify
from src.onboard.assemble import WikiExtract
from src.onboard.beat_draft import (
    AnthropicBeatDrafter,
    CostNotConfirmed,
    MockBeatDrafter,
    draft_all,
    estimate_cost,
    planned_beat_count,
)
from src.schema.definitions import MVP_LENSES, TAGGABLE_LENSES

# Parent (genre) lens names — never taggable; a beat tagged with one of these
# would tag a parent Lens node and trip test_no_parent_lens_in_city_beats.
_PARENT_LENS_NAMES = {lens["name"] for lens in MVP_LENSES}

# A 4-sentence lead splits into exactly 3 spans (front-loaded 2/1/1), so every
# fixture POI yields 3 beats. Kept as a named constant the assertions read from.
_BEATS_PER_POI = 3


# ---------------------------------------------------------------------------
# Fixtures — real multi-sentence Wikipedia-lead text (≥4 sentences each, so the
# split yields ≥3 spans and the establishing span is ≥2 sentences — enough for
# the grounding gate's ≥2-fragment paraphrase undo to actually trip).
# ---------------------------------------------------------------------------


def _extracts() -> list[WikiExtract]:
    return [
        WikiExtract(
            poi_name="Brooklyn Bridge",
            revid="111",
            text=(
                "The Brooklyn Bridge is a hybrid cable-stayed suspension bridge in New "
                "York City, spanning the East River between the boroughs of Manhattan "
                "and Brooklyn. Opened on May 24, 1883, it was the first fixed crossing "
                "of the East River. It was also the longest suspension bridge in the "
                "world at the time of its opening, with a main span of 1,595.5 feet. "
                "The bridge was designed by John Augustus Roebling."
            ),
            article_title="Brooklyn Bridge",
            url="https://en.wikipedia.org/wiki/Brooklyn_Bridge?oldid=111",
        ),
        WikiExtract(
            poi_name="Central Park",
            revid="222",
            text=(
                "Central Park is an urban park in New York City located between the "
                "Upper West Side and Upper East Side of Manhattan. It is the fifth-largest "
                "park in the city, covering 843 acres. Central Park is the most visited "
                "urban park in the United States, with an estimated 42 million visitors "
                "annually. It is also one of the most filmed locations in the world."
            ),
            article_title="Central Park",
            url="https://en.wikipedia.org/wiki/Central_Park?oldid=222",
        ),
        WikiExtract(
            poi_name="Empire State Building",
            revid="333",
            text=(
                "The Empire State Building is a 102-story Art Deco skyscraper in the "
                "Midtown South neighborhood of Manhattan in New York City. The building "
                "was designed by Shreve, Lamb and Harmon and built from 1930 to 1931. "
                "Its name is derived from Empire State, the nickname of the state of New "
                "York. The building stood as the world's tallest for nearly 40 years."
            ),
            article_title="Empire State Building",
            url="https://en.wikipedia.org/wiki/Empire_State_Building?oldid=333",
        ),
    ]


def _pois() -> list[dict]:
    # ``city_name`` mirrors what ``assemble`` stamps on every POI — it is the
    # source of each beat's city prefix (beat_draft reads poi["city_name"]).
    return [{"name": e.poi_name, "city_name": "testville"} for e in _extracts()]


def _by_slug(extracts: list[WikiExtract]) -> dict[str, WikiExtract]:
    return {slugify(e.poi_name): e for e in extracts}


def _write_corpus(root: Path, slug: str, extracts: list[WikiExtract], beats: list[dict]) -> Path:
    """Mirror ``assemble.write_city``'s on-disk layout for one city:
    ``data/{slug}/wikipedia/{poi_slug}-rev-{revid}.txt`` (verbatim pinned text)
    plus ``data/{slug}/beats.json``. Returns the beats.json path."""
    city_dir = root / "data" / slug
    wiki_dir = city_dir / "wikipedia"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    for ext in extracts:
        poi_slug = slugify(ext.poi_name)
        (wiki_dir / f"{poi_slug}-rev-{ext.revid}.txt").write_text(ext.text, encoding="utf-8")
    beats_path = city_dir / "beats.json"
    beats_path.write_text(json.dumps(beats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return beats_path


# ---------------------------------------------------------------------------
# 1. Multi-beat shape — the density-gate fix.
# ---------------------------------------------------------------------------


def test_each_poi_yields_at_least_three_beats(tmp_path: Path) -> None:
    """A POI with a 4-sentence lead drafts ≥ 3 beats (so it can anchor). One beat
    per POI would leave the tour engine with zero anchor candidates and it would
    refuse (RED) at every duration — this is the whole point of the split."""
    extracts = _extracts()
    beats = draft_all(_pois(), _by_slug(extracts), drafter=MockBeatDrafter())
    by_poi: dict[str, list[dict]] = {}
    for b in beats:
        by_poi.setdefault(b["poi_name"], []).append(b)
    assert len(by_poi) == len(extracts)
    for poi_name, poi_beats in by_poi.items():
        assert len(poi_beats) >= 3, f"{poi_name} drafted only {len(poi_beats)} beat(s)"


def test_beats_are_city_prefixed_grounded_and_distinct(tmp_path: Path) -> None:
    """For one POI: every beat_id is CITY-PREFIXED and unique, every
    source_passage is a distinct VERBATIM substring of the extract, and the lens
    is a TAGGABLE child (never a parent genre)."""
    extract = _extracts()[0]
    poi = {"name": extract.poi_name, "city_name": "testville"}
    beats = draft_all([poi], {slugify(extract.poi_name): extract}, drafter=MockBeatDrafter())

    assert len(beats) >= 3
    ids = [b["beat_id"] for b in beats]
    assert len(set(ids)) == len(ids), f"duplicate beat_id: {ids}"
    assert all(b["beat_id"].startswith("testville_") for b in beats), ids

    passages = [b["source_passage"] for b in beats]
    assert len(set(passages)) == len(passages), "source_passages must be distinct spans"
    for p in passages:
        assert p in extract.text, f"source_passage is not a verbatim substring: {p!r}"

    for b in beats:
        assert b["lens"] in TAGGABLE_LENSES, f"{b['lens']} is not taggable"
        assert b["lens"] not in _PARENT_LENS_NAMES, f"{b['lens']} is a parent lens"


def test_mock_body_is_a_real_sentence_not_a_colon_fragment(tmp_path: Path) -> None:
    """The mock ``script_body`` is a real, verbatim sentence lifted from the lead
    — never the old ``"POI: fragment."`` stub (which is not a substring of the
    source text and reads like junk)."""
    extract = _extracts()[0]
    poi = {"name": extract.poi_name, "city_name": "testville"}
    beats = draft_all([poi], {slugify(extract.poi_name): extract}, drafter=MockBeatDrafter())
    for b in beats:
        body = b["script_body"]
        assert body in extract.text, f"body is not verbatim source text: {body!r}"
        assert not body.startswith(f"{extract.poi_name}:"), f"colon-fragment junk: {body!r}"
        assert body.endswith((".", "!", "?")), body
        assert len(body.split()) >= 5, body


# ---------------------------------------------------------------------------
# 2. Beats pass validate_beats unchanged (grounding + identity + hash).
# ---------------------------------------------------------------------------


def test_mock_beats_pass_validate_beats(tmp_path: Path) -> None:
    extracts = _extracts()
    beats = draft_all(_pois(), _by_slug(extracts), drafter=MockBeatDrafter())
    assert len(beats) >= 3 * len(extracts)
    beats_path = _write_corpus(tmp_path, "testville", extracts, beats)
    errors = validate_beats.validate(beats_path)
    assert errors == [], errors


def test_paraphrased_source_passage_fails_grounding(tmp_path: Path) -> None:
    """UNDO: a drafter that PARAPHRASES source_passage (not a verbatim slice of
    the pinned text) is a memory-reconstruction and must trip the Wikipedia
    grounding gate. (The gate fires only when ≥2 fragments are ungrounded, so a
    single-word edit cannot exercise it — the whole passage drifts, which is why
    each POI's establishing span carries ≥2 sentences.)"""
    extracts = _extracts()
    beats = draft_all(_pois(), _by_slug(extracts), drafter=MockBeatDrafter(paraphrase=True))
    beats_path = _write_corpus(tmp_path, "testville", extracts, beats)
    errors = validate_beats.validate(beats_path)
    assert any("WIKIPEDIA_UNGROUNDED" in e for e in errors), errors


def test_identity_and_hash_unique_across_pois_and_spans(tmp_path: Path) -> None:
    extracts = _extracts()
    beats = draft_all(_pois(), _by_slug(extracts), drafter=MockBeatDrafter())
    beats_path = _write_corpus(tmp_path, "testville", extracts, beats)
    errors = validate_beats.validate(beats_path)
    assert not [e for e in errors if "IDENTITY_COLLISION" in e or "HASH_COLLISION" in e], errors


def test_identity_check_catches_same_topic_slug(tmp_path: Path) -> None:
    """UNDO: a second beat for the SAME poi with a UNIQUE topic_slug is fine;
    forcing its topic_slug equal to another beat's collapses the identity tuple
    and validate must flag IDENTITY_COLLISION."""
    extracts = _extracts()
    beats = draft_all(_pois(), _by_slug(extracts), drafter=MockBeatDrafter())
    dup = dict(beats[0])
    dup["beat_id"] = beats[0]["beat_id"] + "_dup"
    dup["script_body"] = beats[0]["script_body"] + " A second establishing line."
    dup["script_body_hash"] = hash_body(dup["script_body"])  # keep hashes distinct
    dup["topic_slug"] = beats[0]["topic_slug"] + "_alt"  # UNIQUE topic → no collision

    ok_path = _write_corpus(tmp_path / "ok", "testville", extracts, [*beats, dup])
    ok_errors = validate_beats.validate(ok_path)
    assert not any("IDENTITY_COLLISION" in e for e in ok_errors), ok_errors

    dup["topic_slug"] = beats[0]["topic_slug"]  # same poi + same topic → collision
    bad_path = _write_corpus(tmp_path / "bad", "testville", extracts, [*beats, dup])
    bad_errors = validate_beats.validate(bad_path)
    assert any("IDENTITY_COLLISION" in e for e in bad_errors), bad_errors


# ---------------------------------------------------------------------------
# 2b. Corpus-wide content DEDUP — a dense city must not HASH_COLLISION when two
#     POIs share identical beat content (same-article twins OR a shared sentence).
# ---------------------------------------------------------------------------


def _shared_sentence_extracts() -> list[WikiExtract]:
    """Two DISTINCT POIs whose 4-sentence leads SHARE one identical sentence
    (``"It holds many artifacts."``). The split front-loads 2/1/1, so that shared
    sentence is the SECOND span (a one-sentence span) — and thus an identical
    ``script_body`` AND identical verbatim ``source_passage`` — in BOTH POIs.
    Without the run-wide dedup those two beats share a ``script_body_hash`` and
    ``validate_beats`` HARD-FAILS with HASH_COLLISION."""
    return [
        WikiExtract(
            poi_name="Alpha Museum",
            revid="901",
            text=(
                "The Alpha Museum is a museum in Testville. It opened in 1850. "
                "It holds many artifacts. The collection is famous."
            ),
            article_title="Alpha Museum",
            url="https://en.wikipedia.org/wiki/Alpha_Museum?oldid=901",
        ),
        WikiExtract(
            poi_name="Beta Gallery",
            revid="902",
            text=(
                "The Beta Gallery is a gallery in Testville. It was founded in 1900. "
                "It holds many artifacts. Admission is free."
            ),
            article_title="Beta Gallery",
            url="https://en.wikipedia.org/wiki/Beta_Gallery?oldid=902",
        ),
    ]


def test_shared_sentence_across_pois_dedups_no_hash_collision(tmp_path: Path) -> None:
    """RED-FIRST (undo the dedup in ``draft_all`` -> this goes RED): two POIs share
    one identical lead sentence, so their per-span bodies would collide. The
    run-wide dedup skips the SECOND copy, so every ``script_body_hash`` is unique
    and ``validate_beats`` reports NO HASH_COLLISION on the assembled corpus.
    The FIRST POI (Alpha) keeps all its beats; only Beta's colliding span is skipped."""
    extracts = _shared_sentence_extracts()
    pois = [{"name": e.poi_name, "city_name": "testville"} for e in extracts]
    beats = draft_all(pois, _by_slug(extracts), drafter=MockBeatDrafter())

    # No two emitted beats share a script_body_hash (the collision the gate hates).
    hashes = [b["script_body_hash"] for b in beats]
    assert len(set(hashes)) == len(hashes), f"duplicate script_body_hash survived dedup: {hashes}"

    by_poi: dict[str, list[dict]] = {}
    for b in beats:
        by_poi.setdefault(b["poi_name"], []).append(b)
    # Alpha comes first -> keeps all 3 spans; Beta's shared "It holds many
    # artifacts." span is the duplicate -> skipped, leaving 2 beats.
    assert len(by_poi["Alpha Museum"]) == 3, by_poi["Alpha Museum"]
    assert len(by_poi["Beta Gallery"]) == 2, by_poi["Beta Gallery"]
    shared_body = "It holds many artifacts."
    alpha_bodies = [b["script_body"] for b in by_poi["Alpha Museum"]]
    beta_bodies = [b["script_body"] for b in by_poi["Beta Gallery"]]
    assert shared_body in alpha_bodies, alpha_bodies  # first occurrence survives
    assert shared_body not in beta_bodies, beta_bodies  # duplicate skipped

    # And the whole corpus validates clean — the HASH_COLLISION is gone.
    beats_path = _write_corpus(tmp_path, "testville", extracts, beats)
    errors = validate_beats.validate(beats_path)
    assert not [e for e in errors if "HASH_COLLISION" in e], errors
    assert errors == [], errors  # fully clean, not just collision-free


def test_pure_duplicate_poi_yields_zero_beats(tmp_path: Path) -> None:
    """A POI whose lead is byte-identical to an earlier POI's (a same-article "(2)"
    twin that slipped past the extract-layer dedup) has ALL its beats collide with
    the first POI's — so after the run-wide dedup it emits 0 beats (an acceptable
    beatless nav-POI), NOT a full set of HASH_COLLISION duplicates."""
    original = _extracts()[0]
    twin = WikiExtract(
        poi_name="Brooklyn Bridge Overlook",  # a distinct POI, identical article text
        revid="112",
        text=original.text,
        article_title=original.article_title,
        url="https://en.wikipedia.org/wiki/Brooklyn_Bridge?oldid=112",
    )
    extracts = [original, twin]
    pois = [{"name": e.poi_name, "city_name": "testville"} for e in extracts]
    beats = draft_all(pois, _by_slug(extracts), drafter=MockBeatDrafter())

    by_poi: dict[str, list[dict]] = {}
    for b in beats:
        by_poi.setdefault(b["poi_name"], []).append(b)
    assert len(by_poi["Brooklyn Bridge"]) >= 3
    assert by_poi.get("Brooklyn Bridge Overlook", []) == [], "twin should emit 0 beats"

    beats_path = _write_corpus(tmp_path, "testville", extracts, beats)
    errors = validate_beats.validate(beats_path)
    assert not [e for e in errors if "HASH_COLLISION" in e], errors


# ---------------------------------------------------------------------------
# 3. Live (paid) provider cannot spend without confirm=True; estimate is
#    per-beat (multi-beat), not per-POI.
# ---------------------------------------------------------------------------


class _CountingMessages:
    """A stand-in for ``anthropic.Anthropic().messages`` that counts calls and
    never touches the network."""

    def __init__(self) -> None:
        self.calls = 0
        self.models: list[str] = []

    def create(self, *, model: str, **_kwargs: object) -> SimpleNamespace:
        self.calls += 1
        self.models.append(model)
        # Distinct per call, as a real model's per-span narration would be — so the
        # emitted bodies don't hash-collide (script_body is not grounded; only
        # the mechanically-sliced source_passage is).
        return SimpleNamespace(content=[SimpleNamespace(text=f"Narration {self.calls}.")])


class _CountingClient:
    def __init__(self) -> None:
        self.messages = _CountingMessages()


def test_live_provider_without_confirm_makes_zero_calls() -> None:
    fake = _CountingClient()
    pois = _pois()
    extracts = _extracts()
    expected_beats = planned_beat_count(pois, _by_slug(extracts))
    drafter = AnthropicBeatDrafter(client=fake)
    with pytest.raises(CostNotConfirmed) as exc:
        draft_all(pois, _by_slug(extracts), confirm=False, drafter=drafter)
    assert fake.messages.calls == 0
    # The estimate reflects the full drafted-BEAT count (~3/POI), not the POI count.
    assert expected_beats == _BEATS_PER_POI * len(pois)
    assert exc.value.estimate["beats"] == expected_beats
    assert exc.value.estimate["est_usd"] > 0


def test_live_provider_with_confirm_drafts_each_span(tmp_path: Path) -> None:
    fake = _CountingClient()
    extracts = _extracts()
    pois = _pois()
    expected_beats = planned_beat_count(pois, _by_slug(extracts))
    drafter = AnthropicBeatDrafter(client=fake)
    beats = draft_all(pois, _by_slug(extracts), confirm=True, drafter=drafter)
    # One API call per span (per beat), not per POI.
    assert fake.messages.calls == expected_beats
    assert fake.messages.models == ["claude-opus-4-8"] * expected_beats
    assert len(beats) == expected_beats
    # Even the paid path grounds source_passage in the pinned text (never memory).
    beats_path = _write_corpus(tmp_path, "testville", extracts, beats)
    assert validate_beats.validate(beats_path) == []


# ---------------------------------------------------------------------------
# 4. Cost estimate scales linearly and is positive.
# ---------------------------------------------------------------------------


def test_estimate_cost_scales_and_is_positive() -> None:
    one = estimate_cost(1)
    two = estimate_cost(2)
    assert one["model"] == "claude-opus-4-8"
    assert one["est_usd"] > 0
    assert one["est_input_tokens"] == 1200
    assert one["est_output_tokens"] == 350
    assert two["beats"] == 2
    assert two["est_usd"] == pytest.approx(2 * one["est_usd"], rel=0.01)


# ---------------------------------------------------------------------------
# 5. The drafter fails closed — an unpinned provider never means "the fake".
# ---------------------------------------------------------------------------


def test_get_drafter_fails_closed_when_no_provider_is_pinned(monkeypatch) -> None:
    """An unset ``ONBOARD_PROVIDER`` must RAISE, never quietly hand back the fake.

    ``get_drafter()`` used to return ``MockBeatDrafter`` for an unset value and
    for any unrecognised one. ``scripts/workbench.sh`` pins no drafter, so the
    workbench's Draft Beats button — a workbench-only route, mounted only under
    ``WORKBENCH_API_ENABLED`` — silently drafted every beat with the fake and
    presented the result to a human as drafted content. That is the same defect
    class as the silent-WAV TTS provider, on the editorial side.

    ``"mock"`` deliberately stays SELECTABLE, because several callers opt into it
    explicitly and must keep working for $0: ``Makefile``'s ``onboard-city``
    (``ONBOARD_PROVIDER`` (now REQUIRED — no default)), ``tests/conftest.py``,
    and the CLI's documented free path. What is removed is the DEFAULT — the
    silent fallback that a caller gets without asking.

    This mirrors ``src/audio/provider.py``'s ``get_provider()``, which was made
    to fail closed for exactly the same reason.

    UNDO TEST: restore the ``"mock"`` fallback in ``get_drafter()`` -> RED.
    """
    import src.onboard.beat_draft as beat_draft

    # Unset -> refuse, and say what to set.
    monkeypatch.delenv("ONBOARD_PROVIDER", raising=False)
    with pytest.raises(ValueError) as exc:
        beat_draft.get_drafter()
    assert "ONBOARD_PROVIDER" in str(exc.value), (
        f"the refusal must name the missing pin so an operator can fix it; got: {exc.value}"
    )

    # An unrecognised name must also refuse, not fall through to the fake.
    monkeypatch.setenv("ONBOARD_PROVIDER", "definitely-not-a-drafter")
    with pytest.raises(ValueError):
        beat_draft.get_drafter()

    # The explicit opt-ins keep working, both directions.
    monkeypatch.setenv("ONBOARD_PROVIDER", "mock")
    assert isinstance(beat_draft.get_drafter(), MockBeatDrafter)
    monkeypatch.setenv("ONBOARD_PROVIDER", "anthropic")
    assert isinstance(beat_draft.get_drafter(), AnthropicBeatDrafter)
