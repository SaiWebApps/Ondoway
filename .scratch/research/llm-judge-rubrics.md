# LLM judges and rubrics: what a rigorous PASS/FAIL setup needs

Researched 15 November 2026. Every arXiv ID below was checked against the title of the page fetched. The arXiv HTML pages fetched are the latest version of each paper.

**How to read this file.** Numbers come from the paper body, tables and appendices. Each one names the section or table it came from. Quotes are paraphrased rather than copied, to respect the copyright limit on quoting sources. "Practitioner" means a blog post or vendor guide, not a controlled study. Section 5 lists what was not read in full.

Terms used below, explained once:

- **Judge**: the LLM call that reads a tour and returns a verdict.
- **Criterion**: one thing the judge checks, such as "every stop is under 4 minutes for the 78-year-old".
- **TPR** (true positive rate): of the tours a human failed, the share the judge also fails. Here "positive" means "has a problem".
- **TNR** (true negative rate): of the tours a human passed, the share the judge also passes.
- **Cohen's κ, Scott's π, Krippendorff's α**: agreement scores that subtract the agreement you would get by chance. 1 is perfect; 0 is chance.
- **Macro F1**: the average of two accuracy scores, one for the "met" class and one for the "not met" class. Rare classes count as much as common ones.
- **Spearman / Pearson / Kendall correlation**: how closely two sets of scores move together, from −1 to 1.

---

## 1. The answer in one paragraph

A rigorous setup splits "is this tour good for this person" into a short list of narrow yes/no checks. Most of the checks are written for that person and that route. Each check runs as its own judge call, or as code when a rule can decide it. The judge reasons first and gives its verdict last. It sees the persona facts, the route facts and labelled examples of both passing and failing tours. The PASS/FAIL rule is fixed in code from the check results. It is not a sum where a strong axis can buy back a broken one. The judge is trusted only after it is measured against a human's PASS/FAIL labels on a held-out set that contains real failures. That measurement reports TPR and TNR separately. It is repeated whenever the model, the prompt or the criteria change. Per-person rubrics are built the way HealthBench built them. Experts sort each case into a situation category, and pre-written checks for that category are attached. Experts then add checks written for the specific case, including negative ones. Only the category checks were validated against the judge. Ondoway's current judge (five axes in one call, summed, PASS-only examples, no labelled failures) breaks most of these rules; section 2 maps each one.

## 2. What this means for the current judge (short)

| Current design | What the evidence says | Rule it breaks |
|---|---|---|
| Five 0–2 axes in one call | One focused check per call agrees better with humans. Broad axes give low agreement between judges. | R1, R3 |
| Sum to 10, pass at 7 | A summed score lets "misses the 16:40 train" be offset by "great storytelling". | R10 |
| Three PASS-labelled anchors | Examples should cover both classes. One-sided sets teach one-sided behaviour. | R5, R7 |
| No labelled failures | Without failures you can't measure TPR, so a judge that always says PASS looks perfect. | R7 |
| All tours score 7–10 | Matches measured leniency and the ceiling effect of number scales. Not yet confirmable. | R6 |
| Any check that is arithmetic on route data, such as the train deadline or walking-stint length (not verified which ones the current judge reads) | Keep these in code, never in the LLM. Code checks them exactly. | R1 |

---

## 3. The ten requirements

Each requirement gives the rule, the evidence, the source, and any disagreement.

### R1. Rubric structure: an analytic checklist, written for the case

**Rule.** Break the verdict into narrow, self-contained yes/no criteria. Word each one so "yes" means good, or mark it clearly as a negative check. Each criterion tests one thing that a stranger could decide from the text plus the facts given. Route facts go to code: timings, distances, stint lengths, train deadline. The LLM gets only what needs reading: tone, vocabulary, whether a child would follow it, whether an idiom would confuse a second-language listener.

Typical sizes:

| Source | Criteria per case |
|---|---|
| HealthBench | median 11 per conversation (range 2–48), Table 1 |
| Rubrics as Rewards | 7–20 per prompt, §3.2 |
| CheckEval | about 25 per quality dimension, §6.2 |

**Evidence.**

- **CheckEval (Table 2).** Turning broad Likert dimensions into yes/no checklists raised agreement between different judge models:

  | Method | Krippendorff α across all 12 judge models |
  |---|---|
  | G-Eval (broad 1–5 dimensions) | 0.06–0.09 |
  | CheckEval (yes/no checklist) | 0.45–0.48 |

  For the large models it reached 0.67, close to the 0.7 human raters reach on the same data.
- **TICK (Table 4).** Instruction-specific yes/no checklists, answered one by one, matched human preferences exactly more often:

  | Method | Exact match with human preference |
  |---|---|
  | Asking for a preference | 29.3% |
  | Direct 1–5 scoring | 46.4% |
  | Checklist in the prompt, then one score ("check-then-score") | 48.7% |
  | Answering each checklist item explicitly (TICK) | 52.2% |

  So answering each item explicitly matters, not just showing the checklist.
- **Rubrics as Rewards (§5).** One generic rubric applied to every prompt did worse than rubrics written per prompt. Its own explanation: generic criteria miss the prompt's specific requirements and failure modes.
- **EvalLM**, as summarised by Yan and not read in the original: specific criteria agreed with humans more than general ones.
- **Wording.**
  - CheckEval §3.2 shows that a narrow closed question (are all words spelled correctly?) gets clearer answers than an open "how well" question.
  - HealthBench §1 requires criteria to be self-contained and objective.
  - Anthropic's agent-evals post (Step 2) sets the bar that two domain experts would reach the same verdict independently. It also advises letting the judge answer "Unknown" when it lacks the information (Step 5).
- **Where criteria come from.**
  - EvalGen (§7.3.1) found people cannot fully fix their criteria before they grade outputs. They add and redefine criteria as they see outputs ("criteria drift").
  - Hamel's FAQ says to write the rubric after error analysis, not before.
  - CheckEval §3.1 found that fully LLM-written sub-dimensions drifted from the intended meaning, so it used only human-chosen ones.

**Caveat for long narration.** CheckEval's limitations (§9) note that yes/no checks get weaker on long texts where one part is good and another bad. A tour script is long. Scope criteria to a stop or a segment where the check is local, for example "this stop's narration uses no idioms" for the second-language listener.

### R2. Binary vs Likert vs fine-grained

**Rule.** Each criterion is binary (met / not met, optionally "unknown"). Scales are for ranking, not for gating.

**Evidence for binary.**

- **CheckEval Appendix C.3.** Converting G-Eval's 1–5 outputs to yes/no after the fact raised Fleiss κ only from 0.19 to 0.28. CheckEval scored 0.48. The gain comes from splitting the question into narrow checks, not from the output format alone.
- **G-Eval §2.** On a 1–5 scale one digit dominates (it names 3). Judges output whole numbers even when asked for decimals. The result is many ties and low variance. That is the ceiling Ondoway sees.
- **Judging the Judges §5.4.** Most judges lean towards "correct" when unsure (details in R6).
- **Hamel (practitioner).** Adjacent scale points mean different things to different raters. Raters drift to the middle. Telling scale results apart takes larger samples. Track progress with several binary checks instead ("4 of 5 expected facts present").

**Evidence that favours scales.** Flag this; it is real.

- **G-Eval (Table 1).** Weighting the 1–5 score by token probabilities breaks ties. GPT-4 reached Spearman 0.514 with humans on summaries. Without the weighting it reached 0.502.
- **Prometheus (§5.1).** A 1–5 scale where every level has its own written description, plus a reference answer, reached Pearson 0.897 with humans on 45 custom rubrics. GPT-4 reached 0.882.
- **MT-Bench (Table 5).** GPT-4 scoring single answers 1–10, converted to pairwise verdicts, agreed with experts 85% of the time on non-tie votes. Humans agreed with each other 81%.
- **Rubrics as Rewards (§5).** A judge that read the rubric and gave one 1–10 score produced better trained models than a weighted sum of yes/no checks. This measures training outcomes, not judge accuracy, so it is weak evidence here.
- **TICK §5.2.** Human annotators who answered checklists and then gave a 1–5 score agreed more with each other (α 0.194 → 0.256). The authors kept a holistic score for humans because some responses passed most checks yet deserved a low score (Appendix H.4).

**Reconciliation.** Scales work when the task is ranking or tracking a continuous quantity, and each level is described. Binary works when the output must be a gate and judges must agree with each other. Ondoway needs a gate.

### R3. One criterion per call vs all at once

**Rule.** One criterion, or one failure mode, per judge call. The exception is a group of near-identical checks on the same segment, if your own labelled data shows batching costs nothing.

**Evidence for one per call.**

- **HealthBench §2.** The grader takes each rubric criterion on its own and decides whether it is met.
- **Anthropic agent-evals post, Step 5.** Grade each rubric dimension with a separate, isolated judge rather than one judge for everything.
- **Hamel FAQ.** The most common mistake is asking one judge to catch too many kinds of error. A focused judge is easier to align with labels.
- **G-Eval §2.** The prompt asks for one metric at a time.
- **TICK §3.1.2.** Each checklist question goes to the judge.
- **Yan (practitioner).** Scoring one dimension per prompt usually does better. No number is given.

**Evidence against.** CheckEval §3.3 grouped related questions in one prompt after a pilot found "no noticeable difference". No numbers are reported. This is the only counter-evidence found.

**Cost note.** Calls grow with criteria × tours × repeats. Keep the LLM list short by moving every rule-decidable check to code.

### R4. Reasoning order, and evidence that reasoning hurts

**Rule.** The judge writes its evidence and reasoning first and its verdict last, in a fixed field, with a marker between the two. Then test on your labelled set whether reasoning helps each criterion. For checks that only compare against a stated fact, a short "do not over-think; compare to the facts given" instruction may beat long reasoning.

**Evidence that reasoning helps.**

- **MT-Bench (math table).** On 20 judgments of math answers, the judge wrongly accepted:

  | Prompt | Wrongly accepted |
  |---|---|
  | Default | 14 of 20 |
  | Chain-of-thought | 6 of 20 |
  | Reference answer shown | 3 of 20 |

  The sample is small.
- **G-Eval (Table 1).** With chain-of-thought, average Spearman was 0.514; without it, 0.500. The gain is small.
- **TICK (§3.2.2, Fig. 2).** Reasoning before each checklist answer "substantially" improved question-level accuracy. The values are only in the figure.
- **Prometheus §3.2.** The model is trained to write feedback, then a "[RESULT]" marker, then the score. The authors say the marker prevents degeneration.
- **Hamel's Honeycomb judge.** It writes the critique, then the outcome.
- **Anthropic docs.** Recommend the grader reason before scoring.

**Evidence that reasoning hurts.**

- **Survey (Gu et al., Table 4).** GPT-3.5, pairwise only. Asking for an explanation alongside the verdict lowered human agreement from 54.72% to 52.47%. It cut position consistency from 68.78% to 48.97%. It did improve length-bias accuracy, from 20.59% to 35.29%.
- **Verga (Table 3).** On reference matching, telling GPT-4 not to over-think raised κ from 0.627 to 0.725. The authors think GPT-4 was bringing in outside knowledge instead of comparing to the reference.
- **MT-Bench §3.4.** Even with chain-of-thought, the judge often repeated the answer's own mistake.

**Reconciliation (a reading, not tested).** Reasoning helps on judgment calls. It can hurt on plain matching against given facts, because the judge wanders off the facts.

### R5. Reference answers and few-shot anchors

**Rule.** Give the judge the facts it needs:

- the persona card;
- the route data;
- where possible, a reference for the criterion (for example, the list of facts this stop must mention).

Few-shot examples come only from a "train" slice of labelled data. They cover PASS, FAIL and borderline cases, each with a full written critique. Examples used for tuning or for the final test never appear in the prompt.

**Evidence for references.**

- **Prometheus (Table 6).** Removing the reference answer was the largest single drop: Pearson 0.860 → 0.642. Removing the rubric gave 0.837; removing the feedback training gave 0.668.
- **Prometheus 2 (Table 16).** Even GPT-4's correlation with humans fell without a reference: 0.599 → 0.554 on BiGGen, 0.679 → 0.616 on FLASK.
- **MT-Bench (math table).** Wrong acceptances fell to 3 of 20 with a reference.
- **Rubrics as Rewards (Table 1).** Rubrics written with a reference answer beat rubrics written without one (35.9% vs 32.0% downstream). Weak evidence, because it measures a trained policy.

**Evidence on few-shot examples.**

- **MT-Bench (Table 13).** Three examples (A better, B better, tie) raised GPT-4's consistency when the answer order was swapped from 65.0% to 77.5%. They did not change agreement with humans. For GPT-3.5 they moved the bias from the first answer to the second. They cost about four times as much.
- **Verga (Table 3).** Few-shot raised GPT-4's κ from 0.518 (zero-shot) to 0.627.
- **Yan, citing Luo et al. (not read).** Few-shot results swung with the choice, order and number of examples.
- **Hamel.** Common prompt mistakes are:
  - no critiques in the examples;
  - critiques that are too terse;
  - examples missing the context the judge needs;
  - examples that aren't diverse.
- **Prometheus §3.** Training data was balanced across score levels to prevent "decision bias". By the same logic, an anchor set of only PASS examples tilts the judge towards PASS. That inference is ours, not tested.

### R6. Biases: sizes and fixes

| Bias | Measured size | Source | Fix |
|---|---|---|---|
| **Position** (pairwise only) | Verdict stayed the same when the two answers swapped places: Claude-v1 23.8%, GPT-3.5 46.2%, GPT-4 65.0% | MT-Bench Table 2 | Judge both orders; count a win only if it holds in both (§3.4). Few-shot examples raise consistency. Mostly irrelevant to single-tour PASS/FAIL. |
| **Verbosity / length** | A list padded with rephrased copies of its own items fooled Claude-v1 and GPT-3.5 91.3% of the time, GPT-4 8.7% (23 answers) | MT-Bench table captioned Table 3 | Use a strong judge; keep length a code check; compare outputs of similar length |
| | Correlation between length and score within one model: r −0.053 (GPT-4o) to 0.123 (o3). o3's win rate over GPT-4.1 fell from 72.9% to 63.7% when only similar-length responses were compared | HealthBench §6.6, Table 4 | Report length-controlled comparisons |
| | Accuracy on the length-bias test set: GPT-3.5 20.59%, GPT-4-turbo 91.18% | Survey Table 2 | |
| **Self-preference** | GPT-4 gave itself a 10% higher win rate; Claude-v1 25%. The authors call it inconclusive | MT-Bench §3.3 | |
| | Each judge's largest positive deviation from humans came when it judged its own model | Verga §4.4 | A panel of 3 models from different families: spread of deviations 2.2 points vs 6.1 for GPT-3.5 alone; κ 0.763 / 0.906 / 0.867 on three QA sets (Table 1); about 7× cheaper than GPT-4 |
| | GPT-4 always scored GPT-3.5 summaries higher, even when humans preferred the human-written ones | G-Eval §4 | |
| **Leniency** | When the judge is guessing rather than judging, the chance it says "correct": GPT-4 0.69, Llama-3-70B 0.90, Llama-2-70B 0.99; most judges above 0.5. Some judges accepted the dummy answers "Yes" and "Sure" | Judging the Judges §5.3–5.4, Appendix O | Label failures; measure TNR and TPR separately (R7) |
| | Judges found over 95% of consistent summaries but only 30–60% of inconsistent ones | Yan, citing Luo et al. (not read) | |
| | GPT-4 rated a model 4.7 of 7; humans rated it 2.9 | Yan, citing Shepherd (not read) | |
| **Score ceiling** | One digit dominates a 1–5 scale | G-Eval §2 | Binary checks (R2) |
| **Prompt sensitivity** | κ ranged from 0.518 to 0.725 across six prompt variants | Verga Table 3 | Freeze and version the prompt; re-calibrate when it changes (R7) |
| **Gaming** | A constant irrelevant answer won high win rates. Openers like "Thought process:" fooled judges | Survey §4.3, citing others (not read) | Hold out a test set; review transcripts |

Two related findings:

- **Judging the Judges §4.1.** Judges with over 90% raw agreement could still miss the human score by more than 10 points. Judges with Scott's π above 80 stayed within 5 points.
- **Judging the Judges §5.1.** False positives rose as judge quality fell.

### R7. Calibration: labels, balance, metrics, splits, when to redo it

**Rule.** A trusted domain expert labels tours PASS or FAIL, per criterion where possible, with a written critique. The set must contain real failures for every criterion that matters. Split it, freeze the prompt, test once, and report TPR and TNR. Also report a chance-corrected agreement score next to human-to-human agreement.

**How many labels.**

| Source | Recommendation |
|---|---|
| Hamel FAQ | 100–200 labelled examples per failure mode for an LLM judge |
| Hamel FAQ, debugging section | at least 50 PASS and 50 FAIL |
| Hamel judge post | about 100 per failure mode; below 60 examples the confidence intervals are too wide to conclude much |
| CheckEval §6.2 | used a power analysis to choose 100 items for an agreement interval no wider than 0.2 |
| Judging the Judges §3, Appendix I | 400 questions, bootstrapped to show low variance |

**Splits (Hamel FAQ).**

- Train: 10–20%. These may appear in the prompt.
- Dev: 40–45%. Used to tune.
- Test: 40–45%. Used once, at the end.
- Aim for 30–50 PASS and 30–50 FAIL in both dev and test.
- Never put dev or test examples in the prompt.
- Overfitting to dev can happen even without that leak.

**Class balance.**

- Anthropic agent-evals Step 3: test both "should happen" and "shouldn't happen" cases, because one-sided evals give one-sided optimisation.
- Hamel: if failures are 5% of cases, a judge that always says PASS scores 95% agreement and catches nothing.

**Metrics.**

- **TPR and TNR**, reported separately (Hamel). EvalGen calls the same pair "coverage" (bad outputs caught) and "false failure rate" (good outputs wrongly failed). It combines them as a harmonic mean (Appendix A.3).
- **Chance-corrected agreement.** Judging the Judges recommends Scott's π over raw agreement. It dropped Cohen's κ because of known theoretical problems (Appendix B). Yan quotes the paper's older κ figures: 0.84 for GPT-4 and 0.97 for humans. The current version reports π of 87–88 for the best judges and 96.2 for humans, both on a 0–100 scale. Use the current figures.
- **Macro F1 per criterion.** HealthBench uses it because many criteria are rarely failed (§8.1).
- **Correct the measured failure rate** using the judge's known TPR and TNR (Hamel FAQ, RAG section).

**Compare to human agreement, not to 100%.** Humans disagree a lot on subjective work:

| Source | Human-to-human agreement |
|---|---|
| HealthBench (Table 5) | physician macro F1 0.57–0.73 by theme. The GPT-4.1 grader beat the average physician in 5 of 7 themes and ranked in the top half of physicians in 6 of 7 |
| CheckEval (Table 4) | κ 0.53 for 3 humans |
| TICK (Table 6) | α 0.194–0.256 |
| MT-Bench (Table 5) | 81% between experts |

**Who labels.** This is a disagreement:

- Hamel says one "benevolent dictator" expert sets the standard.
- Anthropic's bar is that two experts reach the same verdict.
- HealthBench and CheckEval measure agreement across several raters.

**When to re-calibrate.**

- The judge model changes. Prometheus §1 warns that proprietary judges change version without notice. The survey §6.5 calls the result "evaluation drift".
- The judge prompt changes (Hamel).
- A new failure mode appears in review (EvalGen §8.1, criteria drift).
- The tour generator changes materially (Hamel: rerun whenever something material changes).
- A check passes everything and stops giving signal (Anthropic Step 7; Hamel).

Hamel also suggests a fresh review of 100 or more traces every 2–4 weeks in production systems.

### R8. Repeated sampling, temperature, majority vs pass^k, flaky CI

**Rule.** Measure how often each case's verdict flips when the judge is re-run on the labelled set. If it flips, take a majority over an odd number of runs (3 or 5) per criterion. Don't average scores. For "the tour generator is reliably good for this persona", generate several tours and require all to pass (pass^k).

**Evidence.**

- **The same judge disagrees with itself.** Prometheus 2 (Table 14) ran each judge 3 times at temperature 1 on 1–5 scoring:

  | Judge | Krippendorff α across 3 runs |
  |---|---|
  | GPT-4 | 0.74–0.84 |
  | Claude-3-Opus | 0.83–0.90 |
  | Smaller open models | 0.20–0.79 |

- **Majority beats mean and best.** Survey Table 4, GPT-3.5, 5 runs:

  | Method | Human agreement |
  |---|---|
  | One run (base) | 54.72% |
  | Majority of 5 | 54.68% |
  | Mean of 5 | 54.72% |
  | Best of 5 | 51.95% |

  Majority helped on some bias tests. Best-of-5 hurt nearly everything. The survey says it ran at temperature 0, so how its 5 runs differed is unclear. Treat these numbers with caution.
- **TICK (Fig. 2).** Majority over k samples, with reasoning, raised question-level accuracy further as k grew. The values are only in the figure.
- **CheckEval Appendix C.4.** Averaging 3 runs at temperature 1 barely moved G-Eval's correlation (0.6387 vs 0.6389).
- **G-Eval §3.1.** Sampled GPT-4 20 times at temperature 1 to estimate score probabilities.
- **Aggregate stability hides per-case variance.** HealthBench (Table 7) shows a spread of about 0.002 in the overall score across 16 runs. That is the mean over 5,000 cases, not one case's verdict. Its "worst-of-k" analysis (§6.4) shows per-case variance is large: o3 scores 60% overall, but its worst-of-16 score is about one third lower. No source reports per-case flip rates for a re-run judge. That is a gap we must measure ourselves.
- **pass@k vs pass^k** (Anthropic agent-evals). pass@k is the chance at least one of k attempts passes. pass^k is the chance all k pass. At a 75% single-run pass rate, pass^3 ≈ 42%. Use pass^k where users expect it to work every time.
- **CI practice** (Hamel FAQ).
  - Keep CI sets small and curated.
  - Prefer deterministic checks in CI.
  - Run expensive LLM judges nightly or before release, scaled to the cost of the failure they catch.
  - Track confidence intervals in monitoring.
- **Anthropic Step 4.** Isolate each trial so shared state does not cause correlated flaky failures.

### R9. Persona-conditioned criteria (the HealthBench pattern)

**How HealthBench builds per-case rubrics** (§2–§4, Appendices B, D, I):

1. **Who writes them.** 262 physicians, picked from 1,021 who applied. 26% passed a paid trial task graded partly on rubric quality. 31 more were removed later, and their annotations were deleted. A physician lead and advisers trained and reviewed them.
2. **Cases from situations.** Physicians listed the situations that mattered. A pipeline turned them into conversations covering user types (layperson, clinician, API builder), languages and settings. Ondoway's analogue: personas × routes × situations such as "rain" or "late start".
3. **Category criteria.** Several physicians sorted each case into one of 2–3 categories for its theme. Examples: emergent / conditionally emergent / non-emergent; health professional / not. If a majority (at least 2) agreed, that category's pre-written criteria were attached. There are 34 such criteria, used 8,053 times. One theme, "expertise-tailored communication", tests exactly whether the answer fits the user (Appendix I).
4. **Case-specific criteria.** For each case a physician wrote the criteria for that conversation, median 11. Each carries points from −10 to +10. Negative points mark behaviour to punish. Criteria must be self-contained and objective (§1–§2).
5. **Grading.** A model grader decides "met / not met" for each criterion separately. The case score is points met divided by the maximum positive points, so it can go negative. The benchmark score is the mean, clipped to 0–1 (Appendix D). There is no gate where one critical miss fails the case.
6. **Validation.** Only the category criteria were checked against physician grades: 60,896 graded examples. The prompt and the criteria wording were tuned until the intent was unmistakable to the grader (§8.1). The case-specific criteria were never cross-checked by a second physician (§9).

**Other sources on tying criteria to a person or case.**

- **Prometheus.** Built for custom per-case rubrics. Its abstract names "child-readability" as an example criterion. Each rubric describes the criterion and every score level.
- **Rubrics as Rewards.** Per-prompt rubrics tagged Essential / Important / Optional / Pitfall beat one generic rubric. Rubrics drafted by an LLM from a reference answer did about as well as human-written ones (34.8% vs 35.9%, Table 1). Without a reference they did worse.
- **TICK (Table 3a).** GPT-4o-drafted checklists produced pass rates that correlated 0.772–0.853 with pass rates from expert-written checklists (Command-R+ drafts: 0.713–0.776). Drafting persona criteria with an LLM and having the expert edit them is therefore reasonable.
- **EvalGen (Table 1).** Letting a human pick and edit criteria beat a fully automatic set. It used fewer checks (4 vs 9) and caught more bad outputs (73% vs 49%).
- **Hamel.** Treat persona as one dimension of the test set, alongside feature and scenario. Report the failure rate per persona so one weak persona can't hide in the average.

**Rule for Ondoway.**

- Write one category card per persona type. Each card lists the must-have and must-not criteria that apply to every tour for that persona. The family card might say every stop has something a 5-year-old can do or look at. The second-language card might ban idioms and long sentences. The traveller's card might require the tour to end with enough slack before 16:40.
- Add tour-specific criteria for the actual route and stops.
- Domain experts write or approve the criteria. For Ondoway that means the owner as the deciding expert, plus a teacher, a guide or an accessibility adviser where that adds knowledge. An LLM may draft them.
- Validate category criteria against labelled tours before trusting them.
- Treat tour-specific criteria as unvalidated until reviewed.

### R10. Aggregating many criteria into one PASS/FAIL

**What the sources do.**

- **Weighted, normalised sum.** HealthBench uses point values. Rubrics as Rewards uses Essential 1.0, Important 0.7, Optional 0.3, Pitfall 0.9. CheckEval uses the share of "yes" answers.
  - CheckEval Appendix C.2: weights learned by regression gave no reliable gain, so it kept equal weights.
  - Rubrics as Rewards: removing the importance labels did not hurt (38.8% vs 37.2%, Table 2). Letting the judge weigh the rubric itself beat a hand-weighted sum. Both results are about training outcomes, so they are weak evidence.
- **Pass-all and severity groups (practitioner).**
  - Hamel reports a "pass-all" rate: a case passes only if every check passes. He also suggests a separate pass-all rate for release-blocking checks versus tolerable ones. He discourages complicated weighted composites because nobody knows what a move from 3.2 to 3.7 means.
  - Anthropic offers three rules: weighted to a threshold, binary (all graders must pass), or a hybrid. It also says to give partial credit where steps are meaningful.
  - EvalGen counts an output as failed if any of its checks fails (Appendix A.3). A study participant asked for separate tolerances per criterion, because some criteria must always pass (§7.3.2).
- **Not tested.** No paper read tested "any critical fail means FAIL" against human PASS/FAIL labels. It is practitioner advice.

**Rule.** Use two tiers, fixed in code.

1. **Hard gates.** Deterministic checks (timings, stints, train deadline) and a few must-have LLM criteria per persona. Any gate failing means FAIL.
2. **Soft criteria.** The share met must clear a threshold. Choose the threshold on the dev split so the verdict best matches the expert's PASS/FAIL labels. Confirm it once on the test split.

Keep per-criterion results visible so every FAIL says why.

**Warning about gates (our arithmetic, not a source).** False FAILs multiply across gates. Ten independent LLM gates, each with a TNR of 0.95, pass a truly good tour only 0.95¹⁰ ≈ 60% of the time. So:

- keep LLM gates few;
- require high TNR on each;
- move everything decidable to code.

---

## 4. Where the sources disagree

| Question | One side | Other side | Reading |
|---|---|---|---|
| Reason before the verdict? | Helps: TICK, MT-Bench math, G-Eval (small), Prometheus, Hamel, Anthropic docs | Hurts: survey Table 4 (GPT-3.5, pairwise); Verga's "don't over-think" result | Helps judgment, may hurt plain fact matching. Test per criterion. |
| One criterion per call? | Yes: Anthropic post, Hamel, HealthBench, G-Eval, Yan | No difference: CheckEval pilot, no numbers | Default to one per call. |
| Binary or scale? | Binary: CheckEval, Hamel, Yan | Scales work for ranking: G-Eval, Prometheus, MT-Bench single grading. The Anthropic docs show 1–5 examples | Binary for gates. |
| Same model as generator? | Use a different one: Anthropic docs (in a code comment) and the survey §4.2.1 | Same is fine once validated: Hamel | Self-preference is measured (Verga, G-Eval). MT-Bench calls it inconclusive. Validate either way, or use a panel. |
| One expert or several? | One deciding expert: Hamel | Two experts agree: Anthropic. Several raters: HealthBench, CheckEval | Use one decider, plus a second rater on a sample to measure agreement. |
| Volume or quality of test cases? | Volume: Anthropic docs say more cases with lower-signal automatic grading beat fewer hand-graded ones | Quality: Hamel (100 diverse beats thousands skimmed); Anthropic post (unambiguous tasks first) | Quality for the labelled calibration set; volume for routine runs. |
| Write checks before features? | Yes: Anthropic agent-evals post | Generally no, write checks for failures you have seen: Hamel | Persona must-haves known in advance are the exception Hamel allows. |
| Few-shot examples | Help consistency and κ: MT-Bench, Verga | Unstable with choice and order: Luo et al. via Yan. No gain in human agreement: MT-Bench | Use them, drawn from the train split, covering both classes. |

## 5. What was and wasn't read in full

**Read in full (body and the appendices that matter):**

- Zheng et al. (MT-Bench): body and Appendix D.
- G-Eval: body.
- Prometheus: body.
- Prometheus 2: body and Appendices B, E, F, G.
- EvalGen: body and Appendix A.3.
- Verga et al.: body.
- CheckEval: body and Appendix C.
- TICK: body.
- Rubrics as Rewards: body.
- HealthBench: body and Appendices B, C, D, the start of I.
- Judging the Judges: body and Appendices B, O.
- Hamel's judge guide and FAQ, Yan's post, Anthropic's agent-evals post, Anthropic's docs page: in full.

**Not read in full:**

- Survey (Gu et al.): §2.2–2.4, §5 and §7 skipped. §2.1, 2.5, 3, 4 and 6 were read.
- HealthBench: Appendix I after the first themes; Appendices A and E–H; the grader prompt in the simple-evals code repository.
- Other appendices: TICK (except H.4 headings), Rubrics as Rewards, Prometheus 1, G-Eval, Verga, CheckEval beyond C.
- Any value that exists only in a figure: TICK Fig. 2, Rubrics as Rewards Fig. 3, Judging the Judges Figs. 1–4, Prometheus Figs. 4–6, HealthBench Figs. 7 and 12. These are described from the text around them.
- Papers known only through Yan's summaries: Luo et al., Shepherd, EvalLM, Liu et al. on pairwise preference.

**Citation traps.**

- MT-Bench's body text and captions are numbered differently. The verbosity attack is captioned Table 3. The math chain-of-thought / reference table is captioned Table 4. Agreement is Table 5. Cite by caption.
- Hamel's judge post is now titled "Using LLM-as-a-Judge For Evaluation: A Complete Guide". It is the same post as "Creating a LLM-as-a-Judge That Drives Business Results".
- Anthropic's old "define success" and "develop tests" doc URLs now both serve one merged page, "Define success criteria and build evaluations".

---

## 6. Per-source notes

**Zheng et al. 2023, Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena (2306.05685).**
- Setup: 80 two-turn questions and 58 expert labellers, giving about 3,000 votes, plus about 3,000 crowd votes from Chatbot Arena.
- GPT-4 agreed with experts 85% of the time on non-tie votes; experts agreed with each other 81% (Table 5).
- Single-answer 1–10 grading by GPT-4 tracked its pairwise verdicts and the human verdicts closely.
- Agreement rose from about 70% to nearly 100% as the quality gap between the two models grew (Table 7). The judge is weakest when outputs are close in quality, which is where Ondoway's tours sit.
- Biases: position (Table 2), verbosity (Table 3), self-preference (inconclusive), and being misled on math the judge itself could solve. Fixes: swap order; few-shot; chain-of-thought; reference (math table).
- Limitation it states: several helpfulness dimensions were collapsed into one score, and splitting them is future work (§6).

**Liu et al. 2023, G-Eval (2303.16634).**
- Method: GPT-4 writes its own evaluation steps from the criterion, then fills in a form scoring one metric. The score is weighted by token probabilities. Average Spearman 0.514 on SummEval (Table 1).
- Removing the probability weighting gives 0.502; removing chain-of-thought gives 0.500.
- On a 1–5 scale one digit dominates, and outputs are whole numbers, so ties are common (§2).
- It scored GPT-3.5 summaries higher than human-written ones even where humans preferred the human ones (§4). Humans' own agreement on that set was very low (Krippendorff α 0.07).

**Kim et al. 2023, Prometheus (2310.08491).**
- Inputs: instruction, response, a custom rubric (the criterion plus a description of each level from 1 to 5) and a reference answer that would score 5. Output: feedback, then the score.
- Pearson 0.897 with humans on 45 rubrics; GPT-4 0.882; GPT-3.5 0.392 (§5.1).
- Ablation (Table 6): removing the reference drops 0.860 → 0.642; removing the rubric, 0.837; removing the feedback, 0.668.
- Data design: equal numbers at each score level, and equal-length references, to avoid decision and length bias (§3).
- Human raters found it more critical than GPT-4 (Fig. 6).

**Kim et al. 2024, Prometheus 2 (2405.01535).**
- Open judge made by merging a 1–5 scoring model with a pairwise model. Pearson with humans on FLASK 0.555, GPT-4 0.679 (Table 3).
- Reference answers help every judge, GPT-4 included (Table 16).
- Self-consistency across 3 runs at temperature 1 is imperfect, even for GPT-4: α 0.74–0.84 (Table 14).
- Stated limitation: it handles only 1–5 or A/B formats, not checklists (Limitations).

**Shankar et al. 2024, Who Validates the Validators? EvalGen (2404.12272).**
- Tool: an LLM suggests criteria; a human edits them; the LLM writes candidate checks as code or judge prompts; the human grades outputs thumbs-up or thumbs-down; the tool keeps the candidate that best matches the grades.
- With human-edited criteria and 16 graded outputs per task, it caught more bad outputs than the fully automatic baseline (product task 73% vs 49%) with fewer checks (Table 1).
- Key finding, "criteria drift": people refine and add criteria as they grade, and sometimes change earlier grades (§7.3.1).
- Users trusted code checks more than LLM checks (§7.4.4). They wanted to grade per criterion (§7.2.3). They wanted different false-failure tolerances per criterion (§7.3.2).
- Only 9 users, and the off-line test used 2 tasks.

**Verga et al. 2024, Replacing Judges with Juries (2404.18796).**
- A panel of three small models from different families (Command R, Haiku, GPT-3.5), combined by majority for yes/no tasks or by mean for scores.
- It beat GPT-4 on κ with humans for QA (Table 1). It correlated with Chatbot Arena rankings at Pearson 0.917, against 0.817 for GPT-4 (Table 2).
- It showed less self-preference and about 7× lower cost (§4.4–4.5).
- GPT-4 was unusually sensitive to prompt wording, with κ 0.518–0.725 (Table 3).
- Settings tested: QA correctness and Arena-Hard only.

**Lee et al., CheckEval (2403.18771).**
- Humans choose sub-dimensions and write seed yes/no questions; an LLM expands and filters them; the score is the share of "yes".
- Tested on 12 judges: similar or better human correlation (Table 1) and much higher agreement between judges (Table 2).
- Binarising Likert scores afterwards does not close the gap (C.3). Learned weights do not help (C.2). Averaging more runs does not help G-Eval (C.4).
- Humans applying the same checklist agree with LLMs at κ 0.49; humans with each other 0.53 (Table 4).
- Its own warning: yes/no checks are weaker on long, mixed-quality texts (§9).

**Cook et al. 2024, TICK (2410.03608).**
- An LLM writes a yes/no checklist from each instruction; the judge answers each item; the score is the share passed.
- LLM checklists resemble expert ones (Table 2). GPT-4o checklists' pass rates correlate 0.77–0.85 with pass rates from human checklists; Command-R+'s, 0.71–0.78 (Table 3a).
- Question-level accuracy against a 3-annotator majority is 0.83 for GPT-4o (Table 3b). Reasoning and majority vote improve it (Fig. 2).
- Exact agreement with humans: 52.2% vs 46.4% for direct scoring (Table 4).
- Checklists also raised agreement between human raters (Table 6).

**Gunjal et al. 2025, Rubrics as Rewards (2507.17746).**
- Uses per-prompt rubrics (7–20 items, labelled Essential / Important / Optional / Pitfall) as training rewards.
- Most results are about training a model, not judge accuracy. The judge-alignment result is only in Fig. 3: rubrics helped every judge size, small judges most.
- Useful design notes (§3.1): ground rubrics in expert knowledge, cover several dimensions including pitfalls, weight by importance, and make each item self-contained.
- Generic rubrics lose to per-prompt rubrics (§5).

**Arora et al. 2025, HealthBench (2505.08775).** See R9 for the rubric mechanics. Other points:
- The grader matched physicians (Table 5) after the prompt and criteria wording were tuned (§8.1). Smaller graders were clearly worse: macro F1 0.580 for GPT-4.1 nano vs 0.709 for GPT-4.1 (Table 6).
- Length bias is small but present (§6.6).
- Worst-of-k shows how unreliable a single case is (§6.4).
- Physicians disagree with each other widely (§9).

**Thakur et al. 2024, Judging the Judges (2406.12624).**
- A clean setting: TriviaQA with reference answers, where humans agree at Scott's π 96.2. Only the largest judges reached π of about 87–88.
- Raw agreement hides score errors of more than 10 points; π above 80 keeps errors within 5 points (§4.1).
- Judges lean towards "correct" (Appendix O). Some accept "Yes"/"Sure" (§5.3). They struggle with partly correct answers (Table 2).
- Detailed prompts help strong judges a little and hurt weak ones (§5.2).
- Ranking models is easier than scoring them: even a substring-contains check ranked models well (§4.2).

**Gu et al., A Survey on LLM-as-a-Judge (2411.15594).** Partly read.
- Gives a taxonomy of biases and robustness attacks (§4.2–4.3).
- Its own small experiment (Table 4, GPT-3.5, pairwise): explanations hurt; self-validation did little; majority of 5 helped slightly; mean and best-of-5 did not; panels depend on which models are in them.
- Its suggested process (§2.5): define what is evaluated, design the prompt, pick a strong model, standardise the output format, and iterate with reliability tests.

**Hamel Husain, Using LLM-as-a-Judge For Evaluation: A Complete Guide (formerly "Creating a LLM-as-a-Judge That Drives Business Results").** Practitioner.
- Process ("critique shadowing"): find one principal domain expert; build a dataset across features, scenarios and personas; the expert gives PASS/FAIL with detailed critiques; fix obvious errors; build the judge with expert critiques as examples; iterate until it agrees; then analyse errors by dimension.
- Honeycomb reached over 90% agreement in three iterations on a roughly balanced set.
- Report TPR and TNR when classes are unbalanced.
- Validation: a train / dev / test split of 10–20 / 40–45 / 40–45, with 30–50 of each label in dev and test.

**Hamel Husain and Shreya Shankar, AI Evals FAQ.** Practitioner.
- Binary over Likert.
- Pass-all rate, with severity groups.
- One judge per failure mode.
- 100–200 labels per judged failure mode.
- Validate any classifier on held-out labels.
- Give the judge only the context its failure mode needs; too much context makes it worse.
- Same model as the generator is fine if validated.
- Prefer code in CI; run judges on a schedule set by their cost and the cost of the failure.
- Re-run error analysis on material change, with about 100 fresh traces every 2–4 weeks.

**Eugene Yan, Evaluating the Effectiveness of LLM-Evaluators.** Practitioner survey of about two dozen papers.
- Prefers binary outputs scored with classification metrics.
- Use direct scoring for objective checks and pairwise comparison for subjective ones.
- One dimension per prompt.
- Correlation metrics are hard to translate into recall on bad outputs.
- Quotes the older κ figures from Judging the Judges.

**Anthropic, Demystifying evals for AI agents (Jan 2026).**
- Graders can be code, model or human.
- Combine graders by weighted threshold, all-must-pass, or a hybrid.
- Tasks must be unambiguous enough that two experts agree, and each task gets a reference solution.
- Use balanced problem sets, isolated judges per dimension, an "Unknown" way out, and calibration against experts.
- Read transcripts; watch for saturation.
- pass@k vs pass^k.
- Start with 20–50 tasks drawn from real failures.

**Anthropic docs, Define success criteria and build evaluations.**
- Criteria should be specific, measurable, achievable and relevant, and most uses need several of them.
- Detailed rubrics, empirical outputs ("correct"/"incorrect" or 1–5), and reasoning before the score.
- Prefers volume of cases over hand-graded quality.
- A code comment says to use a different model from the generator.

---

## 7. Sources

- Zheng et al. 2023. https://arxiv.org/abs/2306.05685 (read at https://arxiv.org/html/2306.05685v4)
- Liu et al. 2023, G-Eval. https://arxiv.org/abs/2303.16634
- Kim et al. 2023, Prometheus. https://arxiv.org/abs/2310.08491
- Kim et al. 2024, Prometheus 2. https://arxiv.org/abs/2405.01535
- Shankar et al. 2024, Who Validates the Validators? https://arxiv.org/abs/2404.12272
- Verga et al. 2024, Replacing Judges with Juries. https://arxiv.org/abs/2404.18796
- Lee et al., CheckEval. https://arxiv.org/abs/2403.18771
- Cook et al. 2024, TICK. https://arxiv.org/abs/2410.03608
- Gunjal et al. 2025, Rubrics as Rewards. https://arxiv.org/abs/2507.17746
- Arora et al. 2025, HealthBench. https://arxiv.org/abs/2505.08775 (code: https://github.com/openai/simple-evals)
- Thakur et al. 2024, Judging the Judges. https://arxiv.org/abs/2406.12624
- Gu et al., A Survey on LLM-as-a-Judge. https://arxiv.org/abs/2411.15594
- Hamel Husain, LLM-as-a-Judge guide. https://hamel.dev/blog/posts/llm-judge/
- Hamel Husain and Shreya Shankar, AI Evals FAQ. https://hamel.dev/blog/posts/evals-faq/
- Eugene Yan, Evaluating the Effectiveness of LLM-Evaluators. https://eugeneyan.com/writing/llm-evaluators/
- Anthropic, Demystifying evals for AI agents. https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Anthropic docs, Define success criteria and build evaluations. https://docs.claude.com/en/docs/test-and-evaluate/define-success (the old develop-tests URL serves the same page)
