"""Tests for src/ingest/prompts — specs/2026-09-10-ingest-slice-3, step 4.

Step 4 (this step) lands the package facade (`src/ingest/prompts/__init__.py`,
re-exporting `prompts.decompose`'s public names) and
`src/ingest/prompts/decompose.py`: AC-19 (DECOMPOSE_PROMPT carries every
pinned rule sentence verbatim, names the four `kind` values, never names
"fact"/"observation", and `render_decompose` substitutes the unit's own
text exactly once via `str.replace` — never `str.format`, since the
prompt's own JSON example carries literal braces), AC-20 (REDO_PROMPT /
render_redo quotes every refusal reason back and refuses to render with no
reasons at all), and AC-21 (P1_RESPONSE_SCHEMA stays inside the structured-
output subset of JSON Schema: only type/properties/required/
additionalProperties/items/enum, every object node's required list equals
its own property names, and the claims[].kind enum is exactly the four
pinned values).

AC-55 (test_ingest_prompts is one of the five ingest test files with a
self-contained test_no_live_client_in_this_file).

Step 11 (this step) extends the file with the prompts.group tests: AC-22
(GROUP_PROMPT_TEMPLATE/GROUP_AMBIGUITY_CLASSES/render_group, including the
call-time TIE_BREAK module lookup and TieBreakMissing on an empty/blank
rule), AC-23 (P2_RESPONSE_SCHEMA stays inside the same structured-output
subset as P1_RESPONSE_SCHEMA), and AC-24 (render_group_redo quotes every
story-refusal reason back). Step 12 extends it again with AC-25 (the
owner's real tie-break text).

This file reads the real chunk text directly off disk (decisions.
test_file_rule's minimal-import spirit, mirrored from
tests/test_ingest_gates.py) rather than going through src.ingest.unit —
prompts.decompose has no dependency on Unit, so this file imports only
src.ingest.prompts, plus (for the P2 schema check) src.ingest.model and
TAGGABLE_LENSES. The tie-break test imports src.ingest.prompts.group
directly, but only INSIDE the test function (never at module level): a
module-level import of a not-yet-built submodule would void collection of
this whole file with an ImportError instead of failing one node
(decisions.test_file_rule).
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

import src.ingest.model as model
import src.ingest.prompts as prompts
from src.schema.definitions import TAGGABLE_LENSES

REPO_ROOT = Path(__file__).resolve().parent.parent
LP_CHUNK_PATH = (
    REPO_ROOT
    / "Books"
    / "new_york"
    / "lonely-planet-new-york-city"
    / "chunk-07-upper-east-side.txt"
)

UNIT_TEXT = LP_CHUNK_PATH.read_text()

#: The rule sentences AC-19 pins verbatim inside DECOMPOSE_PROMPT.
PINNED_SENTENCES = (
    "stands entirely on its own",
    'never write "he", "it", "there", "the same year", "later" or "also"',
    "belongs INSIDE a claim that names both sides",
    "Use your own wording",
    "eight words of the passage in a row",
    "Never write a claim about the book, its route, its pages or its author",
)

#: The four `kind` values the pinned kind-sentence must name, quoted.
PINNED_KIND_WORDS = ('"event"', '"state"', '"belief"', '"ambiguous"')

#: AC-21's allowed JSON-schema key subset (the structured-output subset).
ALLOWED_SCHEMA_KEYS = {"type", "properties", "required", "additionalProperties", "items", "enum"}


def _walk_schema(node: object) -> None:
    """Recursively assert `node` stays inside the structured-output subset.

    Every dict node may only use ALLOWED_SCHEMA_KEYS; every dict node whose
    `type` is `"object"` must set `additionalProperties: False` and a
    `required` list equal to its own `properties` keys.
    """
    if isinstance(node, dict):
        for key in node:
            assert key in ALLOWED_SCHEMA_KEYS, f"schema key {key!r} outside the allowed subset"
        if node.get("type") == "object":
            properties = node.get("properties", {})
            assert node.get("additionalProperties") is False, (
                "object node missing additionalProperties: False"
            )
            assert sorted(node.get("required", [])) == sorted(properties.keys()), (
                "object node's required list must equal its own property names"
            )
        for key, value in node.items():
            if key == "properties":
                # `properties`' own keys are field names, not schema keys —
                # walk each field's SCHEMA, never the properties dict itself.
                for field_schema in value.values():
                    _walk_schema(field_schema)
            else:
                _walk_schema(value)
    elif isinstance(node, list):
        for item in node:
            _walk_schema(item)


def test_decomposer_prompt_carries_the_pinned_rules():
    prompt = prompts.DECOMPOSE_PROMPT

    for sentence in PINNED_SENTENCES:
        assert sentence in prompt, f"missing pinned sentence: {sentence!r}"

    for kind_word in PINNED_KIND_WORDS:
        assert kind_word in prompt, f"missing kind value: {kind_word!r}"

    assert "copied exactly" in prompt
    assert '"fact"' not in prompt
    assert '"observation"' not in prompt

    rendered = prompts.render_decompose(UNIT_TEXT)
    assert rendered.count(UNIT_TEXT) == 1
    assert "{passage}" not in rendered
    assert "{source}" not in rendered


def test_redo_prompt_quotes_every_reason():
    rendered = prompts.render_redo(UNIT_TEXT, ["r1", "r2"])
    assert "r1" in rendered
    assert "r2" in rendered
    assert "from the start" in rendered
    assert UNIT_TEXT in rendered

    with pytest.raises(ValueError):
        prompts.render_redo(UNIT_TEXT, [])


def test_p1_schema_stays_in_the_structured_output_subset():
    _walk_schema(prompts.P1_RESPONSE_SCHEMA)

    kind_enum = (
        prompts.P1_RESPONSE_SCHEMA["properties"]["claims"]["items"]["properties"]["kind"]["enum"]
    )
    assert kind_enum == ["event", "state", "belief", "ambiguous"]

    bad_schema = copy.deepcopy(prompts.P1_RESPONSE_SCHEMA)
    bad_schema["properties"]["claims"]["minItems"] = 1
    with pytest.raises(AssertionError):
        _walk_schema(bad_schema)


def test_grouping_prompt_carries_tie_break_slot(monkeypatch):
    import src.ingest.prompts.group as p_group

    template = prompts.GROUP_PROMPT_TEMPLATE
    assert "{tie_break}" in template
    assert "one story at one place" in template
    assert "exactly once" in template

    assert isinstance(prompts.GROUP_AMBIGUITY_CLASSES, tuple)
    assert len(prompts.GROUP_AMBIGUITY_CLASSES) == 3
    assert all(
        isinstance(ambiguity_class, str) for ambiguity_class in prompts.GROUP_AMBIGUITY_CLASSES
    )
    for ambiguity_class in prompts.GROUP_AMBIGUITY_CLASSES:
        assert ambiguity_class in template

    claims = [("c01", "t1"), ("c02", "t2")]
    rendered = prompts.render_group(claims, tie_break="TIEBREAK-MARKER")
    assert "TIEBREAK-MARKER" in rendered
    assert "c01: t1" in rendered
    assert "c02: t2" in rendered
    assert "{tie_break}" not in rendered
    assert "{claims}" not in rendered

    with pytest.raises(prompts.TieBreakMissing):
        prompts.render_group(claims, tie_break="")
    with pytest.raises(prompts.TieBreakMissing):
        prompts.render_group(claims, tie_break="   ")

    # tie_break=None resolves to the module attribute TIE_BREAK looked up
    # AT CALL TIME (never a def-time default) — monkeypatching the real
    # module and calling with no override proves it both ways: the empty
    # leg still raises (this is TIE_BREAK's real value at step 11) and a
    # freshly-patched non-empty leg is picked up without redefining the
    # function. A def-time default (`tie_break=TIE_BREAK` in the
    # signature) would bind '' forever and green the empty leg for the
    # wrong reason while going stale on the non-empty leg.
    monkeypatch.setattr(p_group, "TIE_BREAK", "")
    with pytest.raises(prompts.TieBreakMissing):
        prompts.render_group(claims)

    monkeypatch.setattr(
        p_group, "TIE_BREAK", "MODULE-LOOKUP-MARKER sentence of enough words to pass"
    )
    rendered_lookup = prompts.render_group(claims)
    assert "MODULE-LOOKUP-MARKER sentence of enough words to pass" in rendered_lookup


def test_p2_schema_stays_in_the_structured_output_subset():
    _walk_schema(prompts.P2_RESPONSE_SCHEMA)

    story_props = prompts.P2_RESPONSE_SCHEMA["properties"]["stories"]["items"]["properties"]
    assert story_props["lenses"]["items"]["enum"] == list(TAGGABLE_LENSES)
    assert story_props["beat_type"]["enum"] == list(model.BEAT_TYPE_VALUES)

    enrichment = story_props["enrichment"]
    expected_fields = {
        "physical_cues",
        "entities",
        "narrative_function",
        "emotional_register",
        "sensory_anchor",
        "inline_foreign_phrases",
        "pronunciation",
        "kid_friendly",
        "sub_location",
        "trigger_address",
    }
    assert set(enrichment["properties"].keys()) == expected_fields
    assert set(enrichment["required"]) == expected_fields
    assert enrichment["properties"]["kid_friendly"]["enum"] == ["yes", "no", "unknown"]

    bad_schema = copy.deepcopy(prompts.P2_RESPONSE_SCHEMA)
    bad_schema["properties"]["stories"]["items"]["properties"]["lenses"]["pattern"] = ".*"
    with pytest.raises(AssertionError):
        _walk_schema(bad_schema)


def test_group_redo_prompt_quotes_every_reason():
    claims = [("c01", "t1"), ("c02", "t2")]
    rendered = prompts.render_group_redo(claims, ["p1", "p2"], tie_break="X")
    assert "p1" in rendered
    assert "p2" in rendered
    assert "X" in rendered
    assert "c01: t1" in rendered
    assert "c02: t2" in rendered

    with pytest.raises(ValueError):
        prompts.render_group_redo(claims, [], tie_break="X")


def test_group_prompt_carries_the_owners_tie_break():
    """AC-25: TIE_BREAK now holds the owner's real Rule-A text, not the
    step-11 sentinel. This node checks shape only (non-empty, no sentinel
    tokens, wired through render_group's call-time module lookup) — the
    anti-fabrication check that TIE_BREAK is byte-identical to
    decisions.rule_a_tie_break_value is the step-12 judge's proof
    requirement, done by reading both files directly, never by this $0
    test reading state.json.
    """
    forbidden = ("{tie_break}", "UNSET", "TODO", "TBD", "placeholder", "proposed default")

    assert isinstance(prompts.TIE_BREAK, str)
    assert len(prompts.TIE_BREAK.split()) >= 12
    for token in forbidden:
        assert token not in prompts.TIE_BREAK

    claims = [("c01", "t1"), ("c02", "t2")]
    rendered = prompts.render_group(claims)
    assert prompts.TIE_BREAK in rendered
    assert "{tie_break}" not in rendered


def test_p4_and_p5_schemas_stay_in_the_structured_output_subset():
    """Slice 5: the narration schema (one string) and the sentence verdict
    schema (a boolean and a sentence) stay inside the same subset as the
    P1-P3 schemas, and the facade re-exports every P4/P5 name."""
    _walk_schema(prompts.P4_NARRATION_SCHEMA)
    _walk_schema(prompts.P5_VERDICT_SCHEMA)
    assert list(prompts.P4_NARRATION_SCHEMA["properties"]) == ["narration"]
    assert list(prompts.P5_VERDICT_SCHEMA["properties"]) == ["entailed", "reason"]

    bad_schema = copy.deepcopy(prompts.P4_NARRATION_SCHEMA)
    bad_schema["properties"]["narration"]["minLength"] = 1
    with pytest.raises(AssertionError):
        _walk_schema(bad_schema)

    for name in (
        "NARRATE_PROMPT",
        "NARRATE_REDO_PROMPT",
        "NARRATE_REVISE_PROMPT",
        "JUDGE_SENTENCE_PROMPT",
        "P4_NARRATION_SCHEMA",
        "P5_VERDICT_SCHEMA",
        "render_narrate",
        "render_narrate_redo",
        "render_narrate_revise",
        "render_judge_sentence",
    ):
        assert name in prompts.__all__, name


def test_narration_prompts_carry_the_voice_rules_and_quote_refusals_back():
    """The P4 prompt tells the author the narration rules CONTEXT.md pins
    (every sentence from a claim, no book, no framing, own wording) and
    carries the claim texts and nothing else; the redo and revise prompts
    quote every reason / refused sentence back verbatim and refuse to
    render with nothing to fix; the P5 prompt carries one sentence and the
    claims. Every slot is substituted by str.replace, so a claim text
    with braces survives."""
    claims = ["The museum opened in 1959.", "Wright designed it {in his 80s}."]
    ask = prompts.render_narrate("Guggenheim Museum", "How it came to be", claims)
    for rule in (
        "Every sentence states something one of the claims says",
        "Never mention a book, a guide, an author",
        '"imagine", "picture", "envision"',
        "Your own wording throughout",
    ):
        assert rule in ask, rule
    assert "Guggenheim Museum" in ask and "How it came to be" in ask
    for claim in claims:
        assert f"- {claim}" in ask
    assert "{claims}" not in ask and "{place}" not in ask and "{title}" not in ask

    redo = prompts.render_narrate_redo(
        "Guggenheim Museum", "How it came to be", claims, "The book says so.",
        ["provenance_leak: 'The book'", "framing: 'Imagine'"],
    )
    assert "The book says so." in redo
    assert "- provenance_leak: 'The book'" in redo and "- framing: 'Imagine'" in redo
    assert "{problems}" not in redo and "{narration}" not in redo
    with pytest.raises(ValueError):
        prompts.render_narrate_redo("P", "T", claims, "x", [])
    with pytest.raises(ValueError):
        prompts.render_narrate_redo("P", "T", claims, "x", ["  "])

    revise = prompts.render_narrate_revise(
        "Guggenheim Museum", "How it came to be", claims, "A. B.",
        [("B.", "no claim says B")],
    )
    assert '- "B.": no claim says B' in revise
    assert "A. B." in revise
    assert "{refusals}" not in revise
    with pytest.raises(ValueError):
        prompts.render_narrate_revise("P", "T", claims, "x", [])

    judge = prompts.render_judge_sentence("The museum opened in 1959.", claims)
    assert "Sentence:\nThe museum opened in 1959." in judge
    assert "- Wright designed it {in his 80s}." in judge
    assert "{sentence}" not in judge and "{claims}" not in judge


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


def test_p6_prompt_defines_same_as_containment_and_names_only_existing_claims():
    """Slice 10's Guggenheim replay (2026-09-18): in both runs the merge
    judge folded "this 1959 masterpiece" (a claim about walking the ramps)
    into "completed in 1959" and "begins on the top floor" into Wright's
    INTENDED route — `same` on a shared date or word, which silently drops
    the new claim's content (a fold keeps the existing text). And in run 1 it
    named another claim of the NEW story as a match, which no answer can
    express. The rules now say `same` means the existing claim states
    everything the new claim states, that sharing an element is not sharing
    a fact, and that existing_claim_id only ever names a listed claim — with
    a worked example from outside every calibration and replay place."""
    for rendered in (prompts.MERGE_PROMPT, prompts.MERGE_REDO_PROMPT):
        assert "states EVERYTHING the new claim states" in rendered
        assert "sharing a date, a name, a place or a word is not sharing a fact" in rendered
        assert "never another claim of the new story" in rendered
        assert "Eiffel Tower" in rendered  # the worked example: not a New York place
        assert "Guggenheim" not in rendered


def test_p6_merge_schema_stays_in_the_structured_output_subset_and_prompt_pins_the_rules():
    """Slice 6: the merge judge's answer schema lives inside the same
    structured-output subset as P1-P5's, with the story and claim verdict
    enums pinned; the prompt carries the non-negotiable rule that a newer
    source is never right by default, shows the judge claim texts with
    kinds, years and stated values and never a span; the redo prompt quotes
    the refused answer and every problem back and refuses to render with
    none. All reached through the facade."""
    _walk_schema(prompts.P6_MERGE_SCHEMA)
    assert prompts.P6_MERGE_SCHEMA["properties"]["story"]["enum"] == ["same", "new", "supersedes"]
    item = prompts.P6_MERGE_SCHEMA["properties"]["claims"]["items"]
    assert item["properties"]["verdict"]["enum"] == ["new", "same", "conflict"]
    assert prompts.STORY_VERDICTS == ("same", "new", "supersedes")
    assert prompts.CLAIM_VERDICTS == ("new", "same", "conflict")
    assert "A newer source is never right by default" in prompts.MERGE_PROMPT

    new_claims = [
        {"claim_id": "c01", "text": "The building was finished in 1959.", "kind": "event",
         "as_of": 2024}
    ]
    existing = [
        {
            "handle": "b1",
            "title": "Y",
            "claims": [
                {"claim_id": "c02", "text": "Finished in 1959.", "kind": "event",
                 "status": "resolved", "as_of": [2023], "stated_values": ["1959"]}
            ],
        }
    ]
    rendered = prompts.render_merge("Guggenheim Museum", "The building", new_claims, existing)
    for slot in ("{place}", "{title}", "{new_claims}", "{existing}"):
        assert slot not in rendered
    assert "- c01 [event, 2024]: The building was finished in 1959." in rendered
    assert 'beat b1 "Y":' in rendered
    assert "  - b1.c02 [event, resolved, 2023]: Finished in 1959. (stated: 1959)" in rendered
    assert "holds no beat at this place" in prompts.render_merge("P", "T", new_claims, [])

    redo = prompts.render_merge_redo(
        "Guggenheim Museum", "The building", new_claims, existing, '{"story": "same"}', ["p1", "p2"]
    )
    assert '{"story": "same"}' in redo
    assert "- p1" in redo and "- p2" in redo
    assert "The building was finished in 1959." in redo
    with pytest.raises(ValueError):
        prompts.render_merge_redo("P", "T", new_claims, existing, "{}", [])
    with pytest.raises(ValueError):
        prompts.render_merge_redo("P", "T", new_claims, existing, "{}", ["  "])


def test_the_claim_judge_reads_the_whole_passage_and_only_refuses_what_it_contradicts():
    """OWNER RULING 2026-09-13 after the proof chunk: at least six of nine
    attempt-one refusals were false — the judge demanded the cited span
    alone state a detail the passage states a clause away ("in 1939 … four
    years later"; "closed in August and June"; Cedar Hill one clause
    earlier). The prompt now says the passage as a whole is the source,
    that arithmetic and reference the passage makes plain count as stated,
    and that the only grounds for refusal are a fact the passage does not
    state or contradicts."""
    text = prompts.JUDGE_CLAIM_PROMPT.lower()
    assert "read the passage as a whole" in text
    assert "four years later" in text  # the worked example of plain arithmetic
    assert "refuse only" in text


def test_p2_offers_practicalities_and_defines_orientation_as_spatial():
    """OWNER RULING 2026-09-13: the P2 schema offers `practicalities` for
    prices, hours, tickets and transport, and the prompt tells the author
    that `stop_orientation` is SPATIAL — where the listener stands, what
    they face, where the entrance or the ramp is — never a listing."""
    enum = prompts.P2_RESPONSE_SCHEMA["properties"]["stories"]["items"]["properties"]["beat_type"]
    assert "practicalities" in enum["enum"]
    text = prompts.GROUP_PROMPT_TEMPLATE.lower()
    assert "practicalities" in text
    assert "stop_orientation" in text and "where the listener stands" in text


def test_the_coverage_check_numbers_claims_and_asks_which_state_more_than_one_fact():
    """Slice 10 step 2: P1 emitted compound claims (Wright + the spiral + the
    Fifth Avenue towers in one claim) against CONTEXT.md's "one atomic
    factual statement", and nothing flagged them. The P3 unit check that
    already looks for omitted facts now also names, by claim id, every
    claim that states more than one fact — with the facts it bundles — while
    a relation the passage itself states (a cause, a contrast) stays one
    fact. Log-only for now: the runner does not re-ask on it."""
    _walk_schema(prompts.P3_OMISSIONS_SCHEMA)
    compound = prompts.P3_OMISSIONS_SCHEMA["properties"]["compound"]["items"]
    assert compound["properties"]["claim_id"] == {"type": "string"}
    assert compound["properties"]["facts"] == {"type": "array", "items": {"type": "string"}}
    assert prompts.P3_OMISSIONS_SCHEMA["required"] == ["omitted", "compound"]

    rendered = prompts.render_omissions([("c01", "A claim."), ("c02", "Another.")], "The text.")
    assert "- c01: A claim." in rendered and "- c02: Another." in rendered
    assert "states more than one fact" in prompts.OMISSIONS_PROMPT
    assert "A relation the passage itself states" in prompts.OMISSIONS_PROMPT


def test_decompose_asks_for_one_fact_per_claim_with_a_worked_example_from_rome():
    """Slice 10 step 2: P1's compound claims (c141 = Wright + the spiral +
    the Fifth Avenue towers, a relative clause; c148 = two facts joined by
    "and"; c156 = a list emitted beside its own items) made the merge judge
    flip between runs. The first ask now requires ONE fact per claim, with a
    worked example that splits all three shapes, from a place no calibration
    or replay uses. The carve-out is only a relation the passage itself
    states — CONTEXT.md's "X caused Y" — never two events that merely follow
    each other, so the "names both sides" rule cannot let a compound back
    in. The re-ask prompt carries the rule too."""
    for rendered in (prompts.DECOMPOSE_PROMPT, prompts.render_redo("P.", ["x"])):
        assert "states exactly one fact" in rendered
        assert "A relation the passage itself states" in rendered
        assert "never two events that merely follow each other" in rendered
        assert "never also a claim that lists them together" in rendered
        assert "Pantheon" in rendered
        assert "Guggenheim" not in rendered and "Eiffel" not in rendered

