"""Tests for src/ingest/gates.py — specs/2026-09-10-ingest-slice-3, steps 2-3.

Step 2 covers AC-5/AC-6 (span_in_unit: whitespace-normalized on both sides,
otherwise exact — case, punctuation and dashes are load-bearing) and AC-7
(lift: LIFT_RUN_LENGTH == 8 consecutive unit words outside an attributed
quotation, measured claim-vs-whole-unit via
scripts.verbatim.run_outside_quotation).

Step 3 covers AC-8 (leak: APPARATUS_RE/LISTING_RE carried verbatim from the
quarantined _APPARATUS/_LISTING in _to_be_deleted/scripts/reauthor_cleanroom.py),
AC-9/AC-10 (self_contained: ANAPHOR_RE carried verbatim from _ANAPHOR, with
its demonstrative-before-a-verb lookahead), AC-11 (default_kind mapping
'ambiguous' -> 'state' and nothing else) and AC-12 (claim_gates aggregating
span, lift, leak, self_contained in that order, one reason each, never
short-circuiting).

Step 9 covers AC-13/AC-14 (resolve_place: exact casefold+whitespace match on
a POI's name and name_variations, else new_poi).

Step 10 (this step) covers AC-15 (every_claim_once: one reason per omitted
id, one per duplicated id), AC-16 (claim_floor: STRUCTURAL_BEAT_TYPES need
>=1 claim, everything else >=2, 0 never passes), AC-17 (lenses_valid,
beat_type_valid, unique_slugs, known_claim_ids) and AC-18 (story_problems,
the P2 aggregate of all of the above plus claim_floor/lenses_valid/
beat_type_valid per story, each defect's reason carrying that story's slug
per planner.md's code table: orphan_claim:<id> | double_booked:<id> |
unknown_claim:<id> | too_few_claims:<slug> | duplicate_slug:<slug> |
bad_lenses:<slug> | bad_beat_type:<slug> | empty_set).

AC-55 (test_ingest_gates is one of the five ingest test files with a
self-contained test_no_live_client_in_this_file).

Fixtures are the pinned, verified spans/claims from
specs/2026-09-10-ingest-slice-3/run-context.md decisions.fixture_pins, read
directly off the real chunk files on disk — this file imports ONLY
src.ingest.gates (decisions.test_file_rule), so it never goes through
src.ingest.unit / load_unit to get at the unit text.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import src.ingest.gates as gates

REPO_ROOT = Path(__file__).resolve().parent.parent
LP_CHUNK_PATH = (
    REPO_ROOT
    / "Books"
    / "new_york"
    / "lonely-planet-new-york-city"
    / "chunk-07-upper-east-side.txt"
)
FROMMERS_CHUNK_PATH = (
    REPO_ROOT / "Books" / "new_york" / "frommers-nyc-2024" / "chunk-04-ch05-midtown.txt"
)
POI_RAW_PATH = REPO_ROOT / "data" / "new_york" / "poi-raw.json"

UNIT_TEXT = LP_CHUNK_PATH.read_text()


def test_span_in_unit_is_whitespace_normalized_only():
    # Pinned span (fixture_pins), reflowed with a doubled space and a line
    # break — whitespace is the only thing this gate folds.
    assert gates.span_in_unit("1071  Fifth Ave,\n at E 89th St", UNIT_TEXT) is None

    empty_reason = gates.span_in_unit("", UNIT_TEXT)
    assert empty_reason
    assert empty_reason.startswith("span_not_in_unit")

    # A real span, but from a different book's chunk entirely — never in
    # this unit's text.
    frommers_span = "405 Lexington Ave. (at 42nd St.)."
    assert frommers_span in FROMMERS_CHUNK_PATH.read_text()
    reason = gates.span_in_unit(frommers_span, UNIT_TEXT)
    assert reason
    assert frommers_span in reason


def test_span_gate_is_exact_on_case_punctuation_and_dashes():
    # Same pinned span (single-spaced, as it sits in the unit), mangled one
    # way at a time. Each must fail even though whitespace is untouched.
    lowercased = "1071 fifth ave, at e 89th st"
    comma_removed = "1071 Fifth Ave at E 89th St"
    en_dash_to_hyphen = "completed in 1959 - after both Wright"  # real text uses U+2013

    assert gates.span_in_unit(lowercased, UNIT_TEXT) is not None
    assert gates.span_in_unit(comma_removed, UNIT_TEXT) is not None
    assert gates.span_in_unit(en_dash_to_hyphen, UNIT_TEXT) is not None


def test_lift_is_eight_words_outside_attributed_quotation():
    # Sub-runs of the pinned fixture_pins lift example, sized to land
    # exactly on the LIFT_RUN_LENGTH boundary.
    eight_words = "was finally completed in 1959 \u2013 after both Wright"
    seven_words = "finally completed in 1959 \u2013 after both Wright"
    attributed = (
        'The critic wrote "was finally completed in 1959 \u2013 after both Wright" '
        "of the museum."
    )
    unattributed = (
        'A plaque shows "was finally completed in 1959 \u2013 after both Wright" '
        "near the door."
    )
    matched_words = "was finally completed in 1959 after both wright"

    assert gates.LIFT_RUN_LENGTH == 8

    reason = gates.lift(eight_words, UNIT_TEXT)
    assert reason
    assert reason.startswith("lift")
    assert matched_words in reason

    assert gates.lift(seven_words, UNIT_TEXT) is None

    # "wrote" is an attribution cue (scripts.verbatim._ATTRIBUTION_CUES):
    # the quoted run is exempt, so the claim passes.
    assert gates.lift(attributed, UNIT_TEXT) is None

    # "shows" is not a cue: the same run, unattributed, is caught.
    unattributed_reason = gates.lift(unattributed, UNIT_TEXT)
    assert unattributed_reason
    assert matched_words in unattributed_reason


def test_leak_catches_apparatus_and_listing_furniture():
    apparatus_page = "See page 139 for the hours."
    apparatus_step = "Step 4 of this walk turns left."
    apparatus_author = "The author recommends the ramp."
    listing_phone = "Call 212-423-3500 for tickets."
    listing_url = "Tickets at www.guggenheim.org."
    clean = "The museum stands at 1071 Fifth Avenue."

    for furniture in (apparatus_page, apparatus_step, apparatus_author, listing_phone, listing_url):
        reason = gates.leak(furniture)
        assert reason, furniture
        assert reason.startswith("leak")

    assert gates.leak(clean) is None


def test_self_contained_refuses_bare_pronoun_subjects():
    dangling = (
        "He designed it in 1943.",
        "It opened in 1959.",
        "Later the ramp was widened.",
        "That building drew critics.",
        "The same year the museum opened.",
    )
    for claim in dangling:
        reason = gates.self_contained(claim)
        assert reason, claim
        assert reason.startswith("not_self_contained")

    assert gates.self_contained("Wright designed the museum in 1943.") is None


def test_demonstrative_before_a_verb_is_self_contained():
    # The lookahead in _ANAPHOR (carried into ANAPHOR_RE) treats a
    # demonstrative followed by is/are/was/were as a complete sentence,
    # not a dangling reference to a neighbor.
    assert gates.self_contained("That is the only Wright building in Manhattan.") is None
    assert gates.self_contained("This was the first museum on the block.") is None


def test_kind_default_maps_ambiguous_to_state_and_nothing_else():
    assert gates.KIND_RESPONSE_VALUES == ("event", "state", "belief", "ambiguous")
    assert gates.default_kind("ambiguous") == "state"
    for kind in ("event", "state", "belief"):
        assert gates.default_kind(kind) == kind
    for bad in ("fact", "observation", "", "Event", None):
        assert gates.default_kind(bad) is None


def test_claim_gates_names_every_failing_gate_once():
    clean_span = "1071 Fifth Ave, at E 89th St"
    clean_claim = (
        "The Solomon R. Guggenheim Museum stands at 1071 Fifth Avenue "
        "on the corner of East 89th Street."
    )
    assert gates.claim_gates(clean_claim, clean_span, UNIT_TEXT) == []

    # A single claim that fails all four gates: its span is fabricated
    # (never in the unit), it lifts the pinned eight-word run, it leaks
    # guidebook apparatus ("page 139"), and it opens on a bare "It".
    failing_claim = (
        "It was finally completed in 1959 \u2013 after both Wright. "
        "See page 139 for the hours."
    )
    fabricated_span = "This span was never in any chunk of this book at all."

    reasons = gates.claim_gates(failing_claim, fabricated_span, UNIT_TEXT)
    assert len(reasons) == 4
    assert reasons[0].startswith("span_not_in_unit")
    assert reasons[1].startswith("lift")
    assert reasons[2].startswith("leak")
    assert reasons[3].startswith("not_self_contained")


def test_place_resolves_exactly_or_is_flagged_new_poi():
    pois = [
        {
            "name": "Solomon R. Guggenheim Museum",
            "name_variations": ["The Guggenheim"],
            "parent_poi": None,
        },
        {
            "name": "Metropolitan Museum of Art",
            "name_variations": ["The Met"],
            "parent_poi": None,
        },
        {
            "name": "Great Hall",
            "name_variations": [],
            "parent_poi": "Ellis Island",
        },
    ]

    # Casefold + whitespace match on the canonical name itself.
    assert gates.resolve_place("solomon r.  guggenheim museum", pois) == (
        "Solomon R. Guggenheim Museum",
        False,
    )

    # A name_variation resolves to the canonical name, not the variation.
    assert gates.resolve_place("the guggenheim", pois) == (
        "Solomon R. Guggenheim Museum",
        False,
    )

    # No prefix/fuzzy match: a bare substring of the canonical name is a
    # new_poi candidate, returned as given.
    assert gates.resolve_place("Guggenheim", pois) == ("Guggenheim", True)

    # No pois at all -> always a new_poi candidate.
    assert gates.resolve_place("Neue Galerie", []) == ("Neue Galerie", True)


def test_real_poi_file_resolves_the_guggenheim_canonical_name_only():
    # AC-14 / PO Risk 1: against the real poi-raw.json (402 entries), only
    # the exact (casefold+ws) name matches — 'Guggenheim Museum' is not the
    # POI's full name and is a new_poi candidate until a later slice owns a
    # matcher.
    pois = json.loads(POI_RAW_PATH.read_text())
    assert len(pois) == 402

    assert gates.resolve_place("solomon r. guggenheim museum", pois) == (
        "Solomon R. Guggenheim Museum",
        False,
    )
    assert gates.resolve_place("Guggenheim Museum", pois) == ("Guggenheim Museum", True)


def test_every_claim_once_reports_omission_and_duplication():
    # A clean partition: every claim assigned to exactly one story.
    assert gates.every_claim_once([["c01", "c02"], ["c03"]], ["c01", "c02", "c03"]) == []

    # c02 never appears in any story's claim_ids: exactly one orphan reason.
    omission_only = gates.every_claim_once([["c01"], ["c03"]], ["c01", "c02", "c03"])
    assert len(omission_only) == 1
    assert omission_only[0].startswith("orphan_claim:c02")

    # c02 is assigned to two stories, c03 to none.
    both = gates.every_claim_once([["c01", "c02"], ["c02"]], ["c01", "c02", "c03"])
    assert len(both) == 2
    assert any(reason.startswith("double_booked:c02") for reason in both)
    assert any(reason.startswith("orphan_claim:c03") for reason in both)


def test_claim_floor_exempts_only_structural_types():
    # Structural beat_types need only one claim.
    for beat_type in ("stop_orientation", "transit", "sidebar"):
        assert gates.claim_floor(beat_type, 1) is None
    # Everything else needs two.
    assert gates.claim_floor("anecdote", 2) is None

    for beat_type, n in (("anecdote", 1), ("event", 1), ("stop_orientation", 0)):
        reason = gates.claim_floor(beat_type, n)
        assert reason, (beat_type, n)
        assert reason.startswith("too_few_claims")


def test_lenses_beat_type_and_slug_gates():
    assert gates.lenses_valid(["historic_arch", "visual_art"]) is None
    for bad_lenses in ([], ["historic_arch", "historic_arch"], ["architecture"]):
        reason = gates.lenses_valid(bad_lenses)
        assert reason, bad_lenses
        assert reason.startswith("bad_lenses")

    assert gates.beat_type_valid("anecdote") is None
    story_reason = gates.beat_type_valid("story")
    assert story_reason
    assert story_reason.startswith("bad_beat_type")

    assert gates.unique_slugs(["a", "b"]) == []
    duplicate = gates.unique_slugs(["a", "a"])
    assert len(duplicate) == 1
    assert duplicate[0].startswith("duplicate_slug:a")

    unknown = gates.known_claim_ids([["c01", "c99"]], ["c01"])
    assert unknown
    assert unknown.startswith("unknown_claim:c99")


def test_story_problems_names_every_failing_gate():
    clean_stories = [
        {
            "title": "The Ramp Opens",
            "beat_type": "anecdote",
            "lenses": ["historic_arch"],
            "claim_ids": ["c01", "c02"],
        },
        {
            "title": "Elevator to the Top",
            "beat_type": "stop_orientation",
            "lenses": ["visual_art"],
            "claim_ids": ["c03"],
        },
    ]
    assert gates.story_problems(clean_stories, ["c01", "c02", "c03"]) == []

    claim_ids = [f"c{n:02d}" for n in range(1, 11)]  # c01..c10; c03 is never used below
    defective_stories = [
        {
            # c01, c02 -> clean pairing; c02 is double-booked with the next story.
            "title": "Ramp Widened",
            "beat_type": "anecdote",
            "lenses": ["historic_arch"],
            "claim_ids": ["c01", "c02"],
        },
        {
            # c02 double-booked here; c99 is not in claim_ids at all.
            "title": "Second Renovation",
            "beat_type": "event",
            "lenses": ["visual_art"],
            "claim_ids": ["c02", "c04", "c99"],
        },
        {
            # anecdote needs >=2 claims; this story has only one -> too_few_claims.
            # Its title/slug is repeated by the next story -> duplicate_slug.
            "title": "Solo Claim",
            "beat_type": "anecdote",
            "lenses": ["visual_art"],
            "claim_ids": ["c05"],
        },
        {
            # Same title as above: duplicate_slug. stop_orientation floor is 1,
            # so its own single claim does not also trip too_few_claims.
            "title": "Solo Claim",
            "beat_type": "stop_orientation",
            "lenses": ["historic_arch"],
            "claim_ids": ["c06"],
        },
        {
            "title": "Bad Lens Story",
            "beat_type": "factoid",
            "lenses": ["x"],
            "claim_ids": ["c07", "c08"],
        },
        {
            "title": "Bad Type Story",
            "beat_type": "story",
            "lenses": ["historic_arch"],
            "claim_ids": ["c09", "c10"],
        },
    ]
    # c03 is in claim_ids but never assigned to any story above -> orphan.

    reasons = gates.story_problems(defective_stories, claim_ids)
    assert len(reasons) == 7, reasons

    assert any(r.startswith("orphan_claim:c03") for r in reasons)
    assert any(r.startswith("double_booked:c02") for r in reasons)
    assert any(r.startswith("unknown_claim:c99") for r in reasons)
    assert any(r.startswith("too_few_claims:solo-claim") for r in reasons)
    assert any(r.startswith("duplicate_slug:solo-claim") for r in reasons)
    assert any(r.startswith("bad_lenses:bad-lens-story") for r in reasons)
    assert any(r.startswith("bad_beat_type:bad-type-story") for r in reasons)

    assert gates.story_problems([], []) == ["empty_set"]
    assert gates.story_problems([], ["c01"]) == ["empty_set"]


def test_no_live_client_in_this_file():
    """AC-55: this $0-spend test file never names a live LLM client or
    reads its API key. Its own body is exempt from the walk below — this
    docstring and the assert messages name those things on purpose to
    describe the rule, which is not the violation the rule guards against.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    self_name = "test_no_live_client_in_this_file"
    forbidden_names = {"AnthropicClient", "anthropic"}
    forbidden_env_var = "ANTHROPIC_API_KEY"

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == self_name:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                assert sub.id not in forbidden_names, f"{sub.id!r} must not appear in this file"
            elif isinstance(sub, ast.Attribute):
                assert sub.attr not in forbidden_names, (
                    f"{sub.attr!r} must not appear in this file"
                )
            elif isinstance(sub, ast.ImportFrom) and sub.module:
                assert sub.module.split(".")[0] not in forbidden_names, (
                    f"from-import of {sub.module!r} must not appear in this file"
                )
            elif isinstance(sub, ast.alias):
                top_level_name = sub.name.split(".")[0]
                assert top_level_name not in forbidden_names, (
                    f"import of {sub.name!r} must not appear in this file"
                )
                assert sub.asname not in forbidden_names, (
                    f"import alias {sub.asname!r} must not appear in this file"
                )
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                assert forbidden_env_var not in sub.value, (
                    "this file must never read ANTHROPIC_API_KEY"
                )
