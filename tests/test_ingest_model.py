"""Tests for src/ingest/model.py — specs/2026-09-09-ingest-slice-1, track A.

Step 1 covered AC-6, AC-10, AC-11, AC-16, AC-18, AC-22 and AC-24 (see
run-context.md for the verbatim text): claims_hash, bind, shape detection
(SHAPE_LEGACY), and the pydantic schema/vocabulary checks (extra='forbid',
as_of, rights_basis, claim kind/status) that src.ingest.model.validate
performs on a new-shape record.

Step 2 adds AC-1 and AC-2: the real four-beat fixture at
fixtures/ingestion/guggenheim-example.json, whose spans are real text from
the two tracked NYC chunks under Books/new_york.

Step 3 adds AC-7, AC-8, AC-9, AC-12, AC-17, AC-20 and AC-23: identity
(BEAT_ID_MISMATCH/BEAT_ID_COLLISION/CLAIM_ID_COLLISION), CLAIM_NO_SOURCE,
CC-BY-SA attribution fields, the contested-values rule, the arc rule
(structural beat_type exemption), and the remaining beat-level vocabularies
(beat_type, lenses, kid_friendly, resolution.by).

Step 4 adds AC-13, AC-14 and AC-15: span verbatim checks inside the
validator itself (SPAN_NOT_VERBATIM, SPAN_CHUNK_MISSING) once `chunks_root`
is passed to `validate()`.

Step 5 adds AC-3 and AC-21: NARRATION_HASH_STALE (a beat's
narration.claims_hash no longer matches a fresh recomputation over its own
claims) and VERDICT_UNBOUND (a claim's or the narration's verdict.bound_to
no longer matches a fresh `bind()` over its current judged text/spans).

Step 6 adds AC-4 and AC-5: JUDGE_IS_AUTHOR, when a claim's or the
narration's verdict.judge_model equals the beat's narration.author_model.

Step 7 adds AC-19: NARRATION_LIFT, when narration.text shares an 8+
consecutive-word run with one of the beat's own claim spans
(Docs/ingestion/rebuild-spec.md — "Lift = 8+ consecutive words shared with
a span").

Step 7.5 swaps the run measure for `scripts.verbatim.run_outside_quotation`,
exempting a run that sits entirely inside a quotation naming who said it.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

from src.ingest.model import bind, claims_hash, validate

from .ingest_helpers import minimal_beat, stamp

REPO_ROOT = Path(__file__).resolve().parent.parent
LEGACY_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "beats_multi_chunk.json"
FIXTURE = REPO_ROOT / "fixtures" / "ingestion" / "guggenheim-example.json"
CHUNKS_ROOT = REPO_ROOT / "Books" / "new_york"

LP_SOURCE = "lonely-planet-new-york-city"
LP_CHUNK = "chunk-07-upper-east-side"
FROMMERS_SOURCE = "frommers-nyc-2024"
FROMMERS_CHUNK = "chunk-05-ch05-uptown"


def _fixture_records() -> list[dict[str, Any]]:
    return json.loads(FIXTURE.read_text())


def _validate_fixture(records: list[dict[str, Any]]) -> list[str]:
    """Run the validator over fixture records, grounded in the real chunks.

    `validate` gains its `chunks_root` parameter in step 4 (span verbatim);
    until then it takes records alone, so pass the root only once the
    signature accepts it. AC-1 names chunks_root=REPO_ROOT/Books/new_york.
    """
    if "chunks_root" in inspect.signature(validate).parameters:
        return validate(records, chunks_root=CHUNKS_ROOT)
    return validate(records)


def _normalized(text: str) -> str:
    """Whitespace-normalized, nothing else (decisions.verbatim_means)."""
    return " ".join(text.split())


# ── claims_hash (AC-18) ──


def test_claims_hash_is_over_resolved_texts_in_order():
    claims = [
        {"text": "First resolved claim.", "status": "resolved"},
        {"text": "Contested claim, ignored.", "status": "contested"},
        {"text": "Second resolved claim.", "status": "resolved"},
    ]
    expected = hashlib.sha256(
        b"First resolved claim.\nSecond resolved claim."
    ).hexdigest()
    assert claims_hash(claims) == expected

    # Changing one resolved claim's status to contested changes the hash.
    demoted = copy.deepcopy(claims)
    demoted[0]["status"] = "contested"
    assert claims_hash(demoted) != claims_hash(claims)

    # Swapping two resolved claims' order changes the hash.
    swapped = [claims[2], claims[1], claims[0]]
    assert claims_hash(swapped) != claims_hash(claims)

    # No resolved claims hashes the empty string.
    none_resolved = [{"text": "Only contested.", "status": "contested"}]
    assert claims_hash(none_resolved) == hashlib.sha256(b"").hexdigest()


# ── bind (AC-22) ──


def test_bind_is_deterministic_and_span_sensitive():
    assert bind("a", "b") != bind("ab")
    assert bind("a\nb") != bind("a", "b")
    assert bind("a") == hashlib.sha256(b"a").hexdigest()
    assert bind("a") == bind("a")
    assert bind("a", "b") == bind("a", "b")


# ── shape detection (AC-6) ──


def test_legacy_record_is_refused():
    legacy_records = json.loads(LEGACY_FIXTURE.read_text())
    record = legacy_records[0]
    assert "claims" not in record
    assert "narration" not in record

    errors = validate([record])

    assert len(errors) == 1
    assert errors[0].startswith("SHAPE_LEGACY")
    assert record["beat_id"] in errors[0]


# ── schema / vocabulary checks on a new-shape record ──


def test_as_of_must_be_year_or_iso_date():
    valid_int = minimal_beat()
    assert validate([valid_int]) == []

    valid_iso = minimal_beat()
    valid_iso["claims"][0]["sources"][0]["as_of"] = "2024-03-01"
    assert validate([valid_iso]) == []

    # Each invalid as_of yields at least one AS_OF_INVALID/SCHEMA_INVALID
    # error and never a different code (a Union-typed field like `int |
    # str` can legitimately produce more than one error — one per branch
    # pydantic tried — so this checks every code, not the count).
    removed = minimal_beat()
    del removed["claims"][0]["sources"][0]["as_of"]
    errors = validate([removed])
    assert errors
    assert all(e.split(" ", 1)[0] in ("AS_OF_INVALID", "SCHEMA_INVALID") for e in errors)

    string_year = minimal_beat()
    string_year["claims"][0]["sources"][0]["as_of"] = "2023"
    errors = validate([string_year])
    assert errors
    assert all(e.split(" ", 1)[0] in ("AS_OF_INVALID", "SCHEMA_INVALID") for e in errors)

    non_integer_year = minimal_beat()
    non_integer_year["claims"][0]["sources"][0]["as_of"] = 2023.5
    errors = validate([non_integer_year])
    assert errors
    assert all(e.split(" ", 1)[0] in ("AS_OF_INVALID", "SCHEMA_INVALID") for e in errors)


def test_rights_basis_outside_vocabulary_is_refused():
    beat = minimal_beat()
    beat["claims"][0]["sources"][0]["rights_basis"] = "fair_use"

    errors = validate([beat])

    assert len(errors) == 1
    assert errors[0].split(" ", 1)[0] in ("RIGHTS_BASIS_INVALID", "SCHEMA_INVALID")


def test_claim_kind_and_status_vocabulary():
    bad_kind = minimal_beat()
    bad_kind["claims"][0]["kind"] = "fact"
    errors = validate([bad_kind])
    assert len(errors) == 1
    assert errors[0].split(" ", 1)[0] in ("CLAIM_KIND_INVALID", "SCHEMA_INVALID")

    bad_status = minimal_beat()
    bad_status["claims"][0]["status"] = "verified"
    errors = validate([bad_status])
    assert len(errors) == 1
    assert errors[0].split(" ", 1)[0] in ("CLAIM_STATUS_INVALID", "SCHEMA_INVALID")


def test_unknown_field_is_refused():
    beat_extra = minimal_beat()
    beat_extra["script_body"] = "leftover from the old shape"
    errors = validate([beat_extra])
    assert len(errors) == 1
    assert errors[0].startswith("SCHEMA_INVALID")

    source_extra = minimal_beat()
    source_extra["claims"][0]["sources"][0]["page"] = 42
    errors = validate([source_extra])
    assert len(errors) == 1
    assert errors[0].startswith("SCHEMA_INVALID")


# ── identity: beat_id / claim_id (AC-7, AC-8) ──


def test_beat_id_mismatch_is_refused():
    beat = minimal_beat()
    # poi_name "Test POI" slugs to "test-poi"; this beat_id uses snake_case
    # instead, so it no longer equals f"{city_name}/{slug(poi_name)}/{story_slug}".
    beat["beat_id"] = "new_york/test_poi/a-test-story"

    errors = validate([beat])

    assert len(errors) == 1
    assert errors[0].startswith("BEAT_ID_MISMATCH")
    assert beat["beat_id"] in errors[0]


def test_duplicate_beat_id_is_refused():
    # Two records sharing a beat_id -> BEAT_ID_COLLISION (once, on the
    # second occurrence).
    beat_a = minimal_beat()
    beat_b = minimal_beat()
    assert beat_a["beat_id"] == beat_b["beat_id"]

    errors = validate([beat_a, beat_b])

    assert len(errors) == 1
    assert errors[0].startswith("BEAT_ID_COLLISION")
    assert beat_a["beat_id"] in errors[0]

    # Two claims in one beat sharing a claim_id -> CLAIM_ID_COLLISION.
    dup_claim_beat = minimal_beat()
    dup_claim_beat["claims"][1]["claim_id"] = dup_claim_beat["claims"][0]["claim_id"]

    errors = validate([dup_claim_beat])

    assert len(errors) == 1
    assert errors[0].startswith("CLAIM_ID_COLLISION")
    assert dup_claim_beat["beat_id"] in errors[0]


# ── CLAIM_NO_SOURCE (AC-9) ──


def test_claim_without_source_is_refused():
    beat = minimal_beat()
    beat["claims"][0]["sources"] = []
    beat = stamp(beat)

    errors = validate([beat])

    assert len(errors) == 1
    assert errors[0].startswith("CLAIM_NO_SOURCE")


# ── CC-BY-SA attribution fields (AC-12) ──


def test_cc_by_sa_requires_attribution_fields(tmp_path):
    wiki_dir = tmp_path / "wikipedia"
    wiki_dir.mkdir()
    (wiki_dir / "rev-1.txt").write_text("The museum opened to the public in 1959.")

    def _wiki_source(**overrides: Any) -> dict[str, Any]:
        source = {
            "source_id": "wikipedia",
            "chunk": "rev-1",
            "span": "The museum opened to the public in 1959.",
            "as_of": "2024-01-01",
            "rights_basis": "cc_by_sa",
            "stated_value": None,
            "article_title": "Solomon R. Guggenheim Museum",
            "url": "https://en.wikipedia.org/w/index.php?oldid=123456",
            "revision_id": "123456",
            "section": "History",
            "retrieved_at": "2026-01-01",
        }
        source.update(overrides)
        return source

    def _beat_with_source(source: dict[str, Any]) -> dict[str, Any]:
        # Single structural claim (stop_orientation is arc-exempt) so this
        # rule's own chunks_root=tmp_path doesn't also have to ground the
        # unrelated second claim minimal_beat() carries — step 4 added span
        # grounding for every claim once chunks_root is passed.
        beat = minimal_beat(beat_type="stop_orientation")
        beat["claims"] = [beat["claims"][0]]
        beat["claims"][0]["sources"] = [source]
        return stamp(beat)

    def _validate(records: list[dict[str, Any]]) -> list[str]:
        # validate() gains chunks_root in step 4 — this rule doesn't need a
        # real chunk file yet, so call whichever signature is live.
        if "chunks_root" in inspect.signature(validate).parameters:
            return validate(records, chunks_root=tmp_path)
        return validate(records)

    # Full attribution -> passes.
    assert _validate([_beat_with_source(_wiki_source())]) == []

    # revision_id missing -> CC_BY_SA_FIELDS_MISSING.
    no_revision = _wiki_source(revision_id=None)
    errors = _validate([_beat_with_source(no_revision)])
    assert len(errors) == 1
    assert errors[0].startswith("CC_BY_SA_FIELDS_MISSING")

    # url present but lacking '?oldid=' -> CC_BY_SA_FIELDS_MISSING.
    bad_url = _wiki_source(url="https://en.wikipedia.org/wiki/Solomon_R._Guggenheim_Museum")
    errors = _validate([_beat_with_source(bad_url)])
    assert len(errors) == 1
    assert errors[0].startswith("CC_BY_SA_FIELDS_MISSING")

    # The five attribution keys set on a non-cc_by_sa source are refused too.
    misplaced = _wiki_source(rights_basis="owned_copy")
    errors = _validate([_beat_with_source(misplaced)])
    assert len(errors) == 1
    assert errors[0].startswith("CC_BY_SA_FIELDS_MISSING")


# ── contested claims need two differing stated_value (AC-17) ──


def test_contested_claim_needs_two_differing_stated_values():
    # One source -> refused (can't have two differing values from one).
    one_source = minimal_beat()
    one_source["claims"][0]["status"] = "contested"
    one_source = stamp(one_source)
    errors = validate([one_source])
    assert len(errors) == 1
    assert errors[0].startswith("CONTESTED_NEEDS_DIFFERING_VALUES")

    # Two sources, both stated_value null -> refused.
    two_null = minimal_beat()
    two_null["claims"][0]["status"] = "contested"
    two_null["claims"][0]["sources"].append(
        copy.deepcopy(two_null["claims"][0]["sources"][0])
    )
    two_null = stamp(two_null)
    errors = validate([two_null])
    assert len(errors) == 1
    assert errors[0].startswith("CONTESTED_NEEDS_DIFFERING_VALUES")

    # Two sources, equal non-null stated_value -> refused.
    two_equal = minimal_beat()
    two_equal["claims"][0]["status"] = "contested"
    two_equal["claims"][0]["sources"][0]["stated_value"] = "1959"
    second = copy.deepcopy(two_equal["claims"][0]["sources"][0])
    two_equal["claims"][0]["sources"].append(second)
    two_equal = stamp(two_equal)
    errors = validate([two_equal])
    assert len(errors) == 1
    assert errors[0].startswith("CONTESTED_NEEDS_DIFFERING_VALUES")

    # Two sources with real spans and differing stated_value -> no error.
    differing = minimal_beat()
    differing["claims"][0]["status"] = "contested"
    differing["claims"][0]["sources"][0]["stated_value"] = "1959"
    differing["claims"][0]["sources"].append(
        {
            "source_id": FROMMERS_SOURCE,
            "chunk": FROMMERS_CHUNK,
            "span": "Visiting this 1959 masterpiece",
            "as_of": 2024,
            "rights_basis": "owned_copy",
            "stated_value": "1958",
        }
    )
    differing = stamp(differing)
    assert validate([differing]) == []


# ── the arc rule: structural beat_types are exempt (AC-20) ──


def test_arc_rule_exempts_structural_beats():
    single_claim = minimal_beat()
    single_claim["claims"] = [single_claim["claims"][0]]
    single_claim = stamp(single_claim)

    errors = validate([single_claim])

    assert len(errors) == 1
    assert errors[0].startswith("ARC_TOO_FEW_CLAIMS")

    for beat_type in ("stop_orientation", "transit", "sidebar"):
        structural = minimal_beat(beat_type=beat_type)
        structural["claims"] = [structural["claims"][0]]
        structural = stamp(structural)
        assert validate([structural]) == []


# ── beat-level vocabularies (AC-23) ──


def test_beat_level_vocabularies():
    bad_beat_type = minimal_beat()
    bad_beat_type["beat_type"] = "literary_heritage"
    errors = validate([bad_beat_type])
    assert len(errors) == 1
    assert errors[0].startswith("BEAT_TYPE_INVALID")

    empty_lenses = minimal_beat()
    empty_lenses["lenses"] = []
    errors = validate([empty_lenses])
    assert len(errors) == 1
    assert errors[0].startswith("LENS_INVALID")

    unknown_lens = minimal_beat()
    unknown_lens["lenses"] = ["ghost_stories"]
    errors = validate([unknown_lens])
    assert len(errors) == 1
    assert errors[0].startswith("LENS_INVALID")

    bad_kid_friendly = minimal_beat()
    bad_kid_friendly["kid_friendly"] = "maybe"
    errors = validate([bad_kid_friendly])
    assert errors
    assert all(e.split(" ", 1)[0] == "KID_FRIENDLY_INVALID" for e in errors)

    bad_resolution_by = minimal_beat()
    bad_resolution_by["claims"][0]["resolution"]["by"] = "auto"
    errors = validate([bad_resolution_by])
    assert errors
    assert all(e.split(" ", 1)[0] == "RESOLUTION_BY_INVALID" for e in errors)


# ── span grounding against chunks_root (AC-13, AC-14, AC-15) ──


def test_span_not_verbatim_is_refused():
    records = _fixture_records()
    mutated = copy.deepcopy(records)
    beat = mutated[1]
    assert beat["beat_id"] == "new_york/guggenheim-museum/how-the-museum-came-to-be"
    claim = beat["claims"][0]
    assert claim["claim_id"] == "c01"
    source = claim["sources"][0]
    assert source["source_id"] == LP_SOURCE
    # Alter one word of an otherwise-verbatim span.
    source["span"] = source["span"].replace("German baroness", "Austrian baroness")

    errors = validate(mutated, chunks_root=CHUNKS_ROOT)

    assert len(errors) == 1
    assert errors[0].startswith("SPAN_NOT_VERBATIM")
    assert beat["beat_id"] in errors[0]
    assert claim["claim_id"] in errors[0]
    assert source["source_id"] in errors[0]


def test_span_matches_across_line_wrap_only():
    span_with_en_dash = (
        "Construction was finally completed in 1959 – after both Wright and "  # noqa: RUF001
        "Guggenheim had passed away."
    )

    def _beat(span: str) -> dict[str, Any]:
        beat = minimal_beat(beat_type="stop_orientation")
        beat["claims"] = [beat["claims"][0]]
        beat["claims"][0]["sources"] = [
            {
                "source_id": LP_SOURCE,
                "chunk": LP_CHUNK,
                "span": span,
                "as_of": 2023,
                "rights_basis": "owned_copy",
                "stated_value": None,
            }
        ]
        return stamp(beat)

    # The real chunk line-wraps this span; whitespace normalization alone
    # (not the en dash) is what makes it match.
    assert validate([_beat(span_with_en_dash)], chunks_root=CHUNKS_ROOT) == []

    # The same span with the en dash replaced by a hyphen is refused — only
    # whitespace is normalized, not punctuation.
    hyphenated = span_with_en_dash.replace("–", "-")  # noqa: RUF001
    errors = validate([_beat(hyphenated)], chunks_root=CHUNKS_ROOT)
    assert len(errors) == 1
    assert errors[0].startswith("SPAN_NOT_VERBATIM")


def test_missing_chunk_is_refused_not_skipped(tmp_path):
    records = _fixture_records()
    missing_root = tmp_path / "nope"
    assert not missing_root.exists()

    errors = validate(records, chunks_root=missing_root)

    total_sources = sum(
        len(claim["sources"]) for record in records for claim in record["claims"]
    )
    assert len(errors) == total_sources
    assert all(e.startswith("SPAN_CHUNK_MISSING") for e in errors)


# ── hash and verdict binding (AC-3, AC-21) ──


def test_stale_narration_hash_is_refused():
    records = _fixture_records()
    mutated = copy.deepcopy(records)
    beat = mutated[1]
    assert beat["beat_id"] == "new_york/guggenheim-museum/how-the-museum-came-to-be"
    # Stale: does not match a fresh recomputation over this beat's claims.
    beat["narration"]["claims_hash"] = "0" * 64

    errors = validate(mutated, chunks_root=CHUNKS_ROOT)

    assert len(errors) == 1
    assert errors[0].startswith("NARRATION_HASH_STALE")
    assert beat["beat_id"] in errors[0]


def test_verdict_not_bound_to_judged_text_is_refused():
    # Claim text edited after judging -> claim.verdict.bound_to no longer
    # equals bind(text, '\n'.join(spans)). Keep narration.claims_hash fresh
    # so this scenario isolates VERDICT_UNBOUND from NARRATION_HASH_STALE
    # (that interaction — an edited resolved claim also going stale on the
    # narration hash — is exactly what test_stale_narration_hash_is_refused
    # already covers).
    edited_text = minimal_beat()
    edited_text["claims"][0]["text"] = "The building opened to widespread acclaim."
    edited_text["narration"]["claims_hash"] = claims_hash(edited_text["claims"])
    errors = validate([edited_text])
    assert len(errors) == 1
    assert errors[0].startswith("VERDICT_UNBOUND")
    assert edited_text["beat_id"] in errors[0]
    assert edited_text["claims"][0]["claim_id"] in errors[0]

    # A source appended after judging changes the joined-span input, so the
    # bound_to computed over the original single source is now stale too.
    appended_source = minimal_beat()
    appended_source["claims"][0]["sources"].append(
        copy.deepcopy(appended_source["claims"][0]["sources"][0])
    )
    errors = validate([appended_source])
    assert len(errors) == 1
    assert errors[0].startswith("VERDICT_UNBOUND")
    assert appended_source["claims"][0]["claim_id"] in errors[0]

    # narration.verdict.bound_to stale (narration text edited after judging)
    # -> VERDICT_UNBOUND naming the narration, not a claim.
    edited_narration = minimal_beat()
    edited_narration["narration"]["text"] = "A wholly different narration."
    errors = validate([edited_narration])
    assert len(errors) == 1
    assert errors[0].startswith("VERDICT_UNBOUND")
    assert edited_narration["beat_id"] in errors[0]


# ── judge independence (AC-4, AC-5) ──


def test_judge_equal_to_author_is_refused():
    beat = minimal_beat()
    beat["claims"][0]["verdict"]["judge_model"] = beat["narration"]["author_model"]

    errors = validate([beat])

    assert len(errors) == 1
    assert errors[0].startswith("JUDGE_IS_AUTHOR")
    assert beat["beat_id"] in errors[0]
    assert beat["claims"][0]["claim_id"] in errors[0]


def test_claim_verdict_not_entailed_is_refused():
    """Slice 4 closes the slice-1 carry-forward: P3 drops a claim the judge
    refuses, so a claim whose verdict says entailed=false must never reach
    disk — the validator refuses it as VERDICT_NOT_ENTAILED, naming the
    beat and the claim."""
    beat = minimal_beat()
    beat["claims"][1]["verdict"]["entailed"] = False

    errors = validate([beat])

    assert len(errors) == 1
    assert errors[0].startswith("VERDICT_NOT_ENTAILED")
    assert beat["beat_id"] in errors[0]
    assert "c02" in errors[0]


def test_narration_judge_equal_to_author_is_refused():
    beat = minimal_beat()
    beat["narration"]["verdict"]["judge_model"] = beat["narration"]["author_model"]

    errors = validate([beat])

    assert len(errors) == 1
    assert errors[0].startswith("JUDGE_IS_AUTHOR")
    assert beat["beat_id"] in errors[0]


# ── the Guggenheim fixture (AC-1, AC-2) ──


def test_fixture_validates_clean():
    """AC-1: the fixture validates with no errors, and every span it claims
    really is in the chunk it names.

    The span check lives here, not only in the validator, because this test
    owns the fixture: altering one fixture span by a word must turn THIS
    node id red (the step's named undo mutation, AC-32), and the validator
    does not learn to check spans until step 4.
    """
    records = _fixture_records()

    assert _validate_fixture(records) == []

    for record in records:
        for claim in record["claims"]:
            for source in claim["sources"]:
                chunk_path = CHUNKS_ROOT / source["source_id"] / f"{source['chunk']}.txt"
                assert chunk_path.exists(), f"missing chunk file {chunk_path}"
                haystack = _normalized(chunk_path.read_text())
                # A bare `in` assertion would dump the whole chunk into the
                # failure report; assert the boolean and name the span instead.
                found = _normalized(source["span"]) in haystack
                assert found, (
                    f"{record['beat_id']} / {claim['claim_id']} / "
                    f"{source['source_id']}: span is not verbatim in "
                    f"{source['chunk']}: {source['span']!r}"
                )


def test_fixture_structure():
    """AC-2: the fixture's shape — four Guggenheim beats, one structural
    beat with a single claim, a two-source claim spanning both books, all
    owned_copy, every hash freshly recomputable."""
    records = _fixture_records()

    assert len(records) == 4
    assert all(r["city_name"] == "new_york" for r in records)
    assert all(r["poi_name"] == "Guggenheim Museum" for r in records)

    beat_ids = [r["beat_id"] for r in records]
    assert len(set(beat_ids)) == 4
    for record in records:
        assert record["beat_id"] == f"new_york/guggenheim-museum/{record['story_slug']}"

    structural = [r for r in records if r["beat_type"] == "stop_orientation"]
    assert len(structural) == 1
    assert len(structural[0]["claims"]) == 1
    others = [r for r in records if r["beat_type"] != "stop_orientation"]
    assert len(others) == 3
    assert all(len(r["claims"]) >= 2 for r in others)

    two_source_claims = [
        claim
        for record in records
        for claim in record["claims"]
        if {(s["source_id"], s["chunk"]) for s in claim["sources"]}
        == {(LP_SOURCE, LP_CHUNK), (FROMMERS_SOURCE, FROMMERS_CHUNK)}
    ]
    assert two_source_claims, "no claim corroborated by both books"

    all_sources = [s for r in records for c in r["claims"] for s in c["sources"]]
    assert all_sources
    assert all(s["rights_basis"] == "owned_copy" for s in all_sources)

    # Every hash field equals a fresh recomputation — no placeholders.
    for record in records:
        for claim in record["claims"]:
            span = "\n".join(s["span"] for s in claim["sources"])
            assert claim["verdict"]["bound_to"] == bind(claim["text"], span)
        narration = record["narration"]
        assert narration["claims_hash"] == claims_hash(record["claims"])
        assert narration["verdict"]["bound_to"] == bind(narration["text"])


# ── narration lift (AC-19) ──


def test_narration_lift_is_refused():
    long_span = "Construction on the museum finally wrapped up after years of delay."
    span_words = long_span.split()

    def _beat_with_narration(narration_text: str) -> dict[str, Any]:
        beat = minimal_beat()
        beat["claims"][0]["sources"][0]["span"] = long_span
        beat["narration"]["text"] = narration_text
        return stamp(beat)

    eight_word_run = " ".join(span_words[:8])
    lifted = _beat_with_narration(f"A short narration. {eight_word_run} Indeed it did.")

    errors = validate([lifted])

    assert len(errors) == 1
    assert errors[0].startswith("NARRATION_LIFT")
    assert lifted["beat_id"] in errors[0]
    # run_outside_quotation reports the matched words case-folded
    # (scripts.verbatim's word contract, inherited as-is — step 7.5).
    assert eight_word_run.lower() in errors[0]

    seven_word_run = " ".join(span_words[:7])
    not_lifted = _beat_with_narration(f"A short narration. {seven_word_run} Indeed it did.")

    assert validate([not_lifted]) == []


def test_attributed_quotation_is_exempt_from_lift():
    """An 8+ word run inside a quotation that names who said it is not a
    lift (scripts.verbatim.run_outside_quotation, decisions — quoting a
    named speaker and citing them is not copying the book that also quoted
    them). The same run inside an unattributed quotation is still refused —
    quotation marks alone earn no exemption.
    """
    long_span = "Construction on the museum finally wrapped up after years of delay."
    span_words = long_span.split()
    eight_word_run = " ".join(span_words[:8])

    def _beat_with_narration(narration_text: str) -> dict[str, Any]:
        beat = minimal_beat()
        beat["claims"][0]["sources"][0]["span"] = long_span
        beat["narration"]["text"] = narration_text
        return stamp(beat)

    attributed = _beat_with_narration(
        f'The entrance carries an inscription: "{eight_word_run}." Visitors pause there often.'
    )
    assert validate([attributed]) == []

    unattributed = _beat_with_narration(
        f'A guidebook once printed the line: "{eight_word_run}." Visitors pause there often.'
    )
    errors = validate([unattributed])

    assert len(errors) == 1
    assert errors[0].startswith("NARRATION_LIFT")
    assert unattributed["beat_id"] in errors[0]
