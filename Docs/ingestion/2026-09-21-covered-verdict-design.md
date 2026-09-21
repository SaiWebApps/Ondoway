# Design note (for owner review, not built): P6 `covered` — one new claim covered by several existing claims

Status: PROPOSED 2026-09-21. Nothing here is implemented. Written by a read-only planning agent from
the code at HEAD b0e6869; file:line references point there. Companion to the merge step 4 result in
`rebuild-spec.md` (the registered rule chose a P1 split; this is the alternative the judge's own
reason supports — the two are not exclusive).

## The case

Frommer's c148 "Frank Lloyd Wright designed the spiral Solomon R. Guggenheim Museum." against the LP
beat's c01 "The Guggenheim Museum building was designed by architect Frank Lloyd Wright." and c03
"The Guggenheim Museum has a spiral ramp." The merge judge answered `new` 11/11 ("no single existing
claim combines design authorship with the spiral shape (b1.c01 + b1.c03)"); the one-way tripwire
matched c01 and held the story every run.

## 1. Answer contract

- Add `"covered"` to `CLAIM_VERDICTS` (prompts/merge.py:25), `ClaimVerdict` (merge.py:106),
  `ClaimOutcomeKind` (merge.py:107). Schema shape unchanged (prompts/merge.py:72-106):
  `existing_claim_id` carries `+`-joined handles, e.g. `"b1.c01+b1.c03"`.
- Rule text after the `same` bullet: `"covered"` when no single claim states everything but two or
  more claims at this place TOGETHER state everything the new claim states, with the same values —
  name every one; sharing words across claims is not sharing a fact; a differing value in any part
  is `conflict` with that claim, never `covered`. Worked example from another city (Eiffel).
- `answer_problems` (merge.py:297-340) — each earns the one re-ask: every `+` part resolves via
  `_claim_ref` (merge.py:288-294); at least 2 distinct parts; no bare id (a claim of the new story);
  `new_value`/`existing_value` empty.
- `resolve_handles` (merge.py:343-358) fills a new `ClaimOutcome.covered_by: list[ClaimRef]`, with
  `existing_beat_id`/`existing_claim_id` = the first ref so run.py:978-981 counts it as a fold and
  run.py:999 excludes it from `kept`.

## 2. What applying it does

- The new claim is ABSORBED, not written; the new story's beat keeps its `new` claims only and is
  re-narrated / queued if below the arc minimum (run.py:994-1030). Job B: c149, c152, c162 remain;
  c151 still folds `same` into LP c21.
- No source is appended to any named claim. The contract runs one way (new ⊆ union); corroborating
  a named claim needs the reverse. c148's span states c01 but not c03's "ramp".
- `resolution.by` (model.py:209) and texts untouched → `claims_hash` (model.py:143-149) stays valid;
  `covered` stays out of `_RESOLVED_TEXTS_CHANGE` (merge.py:568) → no LP rerun.
- `apply` (merge.py:741-770): a `covered: continue` branch; covered beats stay out of `targets`
  (run.py:985-992) and their records are written back unchanged (run.py:1074).
- D9 kinds do not apply (as `same` today, merge.py:751).
- Audit: a new `merge_covered` event (claim id, text, span, source_id, every named {beat, claim,
  text}) replaces the single-text `merge_folded` here (merge.py:638-656).
- Owner decision owed later: a per-part "this named claim is stated entirely" flag that would append
  the source only to those; and `same` has the same asymmetry today (appends `corroborated`,
  merge.py:751-755, though the contract checks only existing ⊇ new).

## 3. Tripwire

In `disagreements` (merge.py:365-389), for `covered`:
`if hinted is not None and hinted not in item["covered_by"]: found.append(...)` — c148 → c01 is no
longer a disagreement; a best match outside the named set still holds. Other branches unchanged.

## 4. Calibration

- `covered_union` (recall against over-splitting): a design claim and a spiral-ramp claim, the
  planted text states both; caught when the outcome is `covered` with exactly the planted set
  (calibrate.py:455-472; `scripted_merge_answer` gets a `covered` branch, calibrate.py:293-338).
- `shared_union` (precision): the planted text shares words with both but states another fact
  ("Frank Lloyd Wright first sketched the Guggenheim's spiral as a ramp for cars"), expected `new`;
  the fixture test (tests/test_ingest_calibrate.py:114-126) asserts `covered` < 0.7 against each
  claim (no hold) and union coverage ≥ 0.7 (the trap is real).
- The `regressions()` baseline (calibrate.py:25) catches a prompt change that erodes
  `shared_element` / `same_fact`.

## 5. RED tests (MockClient, job B's real texts)

| Test | Mutation that must turn it red |
|---|---|
| `merge.merge` with `covered b1.c01+b1.c03` → not held, outcome `covered`, both refs, `rerun == []` | `disagreements` compares `hinted != first ref` |
| a hint outside the set → held | covered branch skips the hint |
| one handle / duplicate / bare `c149` / values given → re-asked once, then held | drop the ≥2 or empty-value check |
| `apply`: named claims byte-identical; `validate` passes | a source appended |
| run P6: c148 covered + c151 same → no `merge_held`; new beat has c149, c152, c162; LP beat identical | `covered` not in `CLAIM_VERDICTS` |

## 6. Risks and review

- Over-folding: union coverage is easier to claim than single-claim containment; a wrong `covered`
  silently drops a true fact from the new story.
- Lost provenance: Frommer's attestation never reaches a claim (does not count toward two-source).
- Review: `merge_covered` lines show the new text, span and every named text; the owner reads all of
  them on the first live job.
- Hold vs apply: `decide` only records a verdict (jobs.py:236-262) — nothing applies an accepted
  `merge_held` item — so holding covered verdicts (run.py:853-870) leaves the Guggenheim where it is.
  Recommendation: APPLY as absorb-without-source (no existing byte changes; the absorbed claim is
  recoverable from P3.json and the event). One constant can instead route `covered` to
  `_queue_held_merge` if the owner prefers D7's caution — it measures the judge but folds nothing.

## 7. Smallest slice and cost

Build ($0): contract + checks + tripwire + `apply` + event + two plants + the tests above. Live
measurement, bars registered first: 10 Guggenheim replays ≈ $0.66 in merge calls (+ one P4/P5 round
per fold for the kept claims); per-run stop $0.25, set ceiling $2.50; bars: c148 `covered` with
{c01, c03} in ≥ 8/10, c151 `same` 10/10, no `covered` on any other Guggenheim claim; the live
calibration run's price printed before any spend (calibrate.py:341).
