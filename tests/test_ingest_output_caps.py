"""Output caps of the model-facing phases — sized for thinking.

Slice 9's first paid job (2026-09-12): the P1 decompose answer for the
Lonely Planet Upper East Side chunk stopped at `max_tokens` = 8,000 having
produced 10,273 characters of JSON (39 complete claims, 22% of the chunk;
recovered by `make ingest-batch`, batch msgbatch_013YWoWeV8u4jNt5uzwF5C7k).
The client sends no `thinking` parameter (`llm._message_kwargs`, pinned by
decisions.sdk_shapes), and claude-opus-5 and claude-sonnet-5 think by
default, so a call's output budget is spent on thinking FIRST and the
answer gets the remainder: 4,013 tokens of text and 3,987 of thinking in
that call (measured with `make ingest-batch ARGS=--text-tokens`).
claude-haiku-4-5 does not think unless asked, so the Haiku phases (P3
verdicts, P5 verdicts) keep their small caps.

The floors below are this session's arithmetic on that measurement (a cap is
a ceiling; spend is billed on tokens produced, so a high cap costs only in
the printed estimate): a whole chunk ~ 177 claims ~ 18k text tokens plus
thinking at the measured 1.0x ~ 36k → P1 ≥ 64k; P2 emits ~44 stories each with
a ten-field enrichment object (5-11k text) plus thinking → ≥ 32k; one
restated claim, one narration, one merge verdict are short text under the
same thinking overhead → ≥ 8k / ≥ 16k / ≥ 16k; the omission check must
list every uncarried fact of a 177-claim passage with its span → ≥ 8k.
"""

from __future__ import annotations

from src.ingest import decompose, group, judge_claims, judge_narration, merge, narrate


def test_the_opus_and_sonnet_phases_leave_room_for_thinking_plus_the_answer():
    assert decompose.P1_MAX_TOKENS >= 64_000
    assert group.P2_MAX_TOKENS >= 64_000  # unbatched: a truncated P2 cannot be recovered
    assert judge_claims.P3_RESTATE_MAX_TOKENS >= 8_000
    assert narrate.P4_MAX_TOKENS >= 16_000
    assert merge.P6_MAX_TOKENS >= 16_000


def test_the_omission_check_can_list_a_whole_passages_uncarried_facts():
    assert judge_claims.P3_OMISSIONS_MAX_TOKENS >= 8_000


def test_the_haiku_verdict_caps_stay_small():
    """A verdict is a boolean, a sentence and a kind; Haiku does not think
    unless asked, so these need no thinking headroom."""
    assert judge_claims.P3_MAX_TOKENS <= 1_000
    assert judge_narration.P5_MAX_TOKENS <= 1_000


def test_plan_rows_price_the_expected_output_not_the_cap():
    """A cap is headroom (thinking first, then the answer); pricing the
    estimate at the cap made the first-pass figure for one chunk $22.62
    of output tokens that will never be produced. Every Opus/Sonnet plan
    row prices an EXPECTED output — thinking-inclusive, measured where a
    run has measured it (P1: 4,013 text + 3,987 thinking for 22% of a
    chunk -> ~36k for a whole chunk) and a stated projection elsewhere —
    while the call still asks for the cap. The Haiku verdict rows have no
    thinking and price their cap as before."""
    assert decompose.P1_EXPECTED_OUTPUT_TOKENS == 36_000
    assert decompose.P1_EXPECTED_OUTPUT_TOKENS < decompose.P1_MAX_TOKENS
    assert all(row.expected_output_tokens == decompose.P1_EXPECTED_OUTPUT_TOKENS
               for row in decompose.P1_PLAN)
    assert group.P2_EXPECTED_OUTPUT_TOKENS < group.P2_MAX_TOKENS
    assert all(row.expected_output_tokens == group.P2_EXPECTED_OUTPUT_TOKENS
               for row in group.P2_PLAN)
    restate = judge_claims.P3_PLAN[1]
    assert restate.role == "author"
    assert restate.expected_output_tokens == judge_claims.P3_RESTATE_EXPECTED_OUTPUT_TOKENS
    assert judge_claims.P3_RESTATE_EXPECTED_OUTPUT_TOKENS < judge_claims.P3_RESTATE_MAX_TOKENS
    assert judge_claims.OMISSIONS_PLAN[0].expected_output_tokens == (
        judge_claims.P3_OMISSIONS_EXPECTED_OUTPUT_TOKENS
    )
    assert narrate.P4_EXPECTED_OUTPUT_TOKENS < narrate.P4_MAX_TOKENS
    assert all(row.expected_output_tokens == narrate.P4_EXPECTED_OUTPUT_TOKENS
               for row in narrate.P4_PLAN)
    revise = judge_narration.P5_PLAN[1]
    assert revise.role == "author"
    assert revise.expected_output_tokens == narrate.P4_EXPECTED_OUTPUT_TOKENS
    assert merge.P6_EXPECTED_OUTPUT_TOKENS < merge.P6_MAX_TOKENS
    assert all(row.expected_output_tokens == merge.P6_EXPECTED_OUTPUT_TOKENS
               for row in merge.P6_PLAN)
    assert judge_claims.P3_PLAN[0].expected_output_tokens == judge_claims.P3_MAX_TOKENS
    assert judge_narration.P5_PLAN[0].expected_output_tokens == judge_narration.P5_MAX_TOKENS
