# Evaluation tools for persona acceptance tests

Researched 15 November 2026. Sources are vendor docs, source files on GitHub, PyPI and npm.
"Unverified" means I could not confirm the point from docs or source.

## What we need, in one paragraph

Each persona is a data file. It holds who the person is, their trip request and their criteria.
Running a persona produces a tour. Deterministic criteria are plain pytest assertions.
Narration criteria need an LLM judge. The judge returns PASS, FAIL or UNKNOWN and quotes evidence.
Results are stored locally and can be viewed. The owner hand-labels results.
From those labels we measure the judge: how many real failures it catches, and how many real passes it passes.
The writer is Claude. The judge may be Claude, OpenAI, Gemini or an open model.

## Short answer

Use **MLflow 3** for the judge, the stored results, the results UI, the owner's labelling and judge tuning.
Use **pytest** for deterministic criteria and the CI gate.
We write a persona loader, one generic judge definition, a pytest wrapper and a small agreement report.
That is roughly 150–250 lines. Detail is in the Recommendation section.

## Versions, licences and owners (checked today)

| Tool | Latest version | Released | Licence | Owner |
|---|---|---|---|---|
| MLflow | 3.18.0 | 2026-11-03 | Apache-2.0 | Databricks |
| DeepEval | 4.3.4 | 2026-11-11 | Apache-2.0 | Confident AI |
| Inspect AI | 0.3.290 | 2026-11-14 | MIT | UK AI Security Institute |
| Promptfoo (npm) | 0.125.1 | 2026-10-25 | MIT | Promptfoo; OpenAI agreed to acquire it in March 2026 (closing not confirmed) |
| Langfuse (SDK) | 5.2.0 | 2026-11-05 | MIT core, plus commercial "ee" add-ons | ClickHouse (acquired 2026) |
| Arize Phoenix | 21.0.0 | 2026-10-30 | Elastic License 2.0 (not OSI open source) | Arize AI |
| Phoenix evals | 3.10.0 | 2026-11-10 | Elastic License 2.0 | Arize AI |
| Opik | 2.2.143 | 2026-11-06 | Apache-2.0 | Comet ML |
| Ragas | 0.4.4 | 2026-11-14 | Apache-2.0 | VibrantLabs (repo moved to `vibrantlabsai/ragas`) |
| openevals | 0.2.5 | 2026-11-12 | MIT | LangChain |
| agentevals | 0.0.9 | 2025-07-24 | MIT | LangChain |
| autoevals | 0.4.1 | 2026-10-21 | MIT | Braintrust |
| Evidently | 0.7.23 | 2026-09-11 | Apache-2.0 | Evidently AI |
| Weave | 0.54.1 | 2026-11-05 | Apache-2.0 (client) | W&B, part of CoreWeave |

Sources: PyPI JSON for each package (e.g. https://pypi.org/pypi/mlflow/json, https://pypi.org/pypi/inspect-ai/json); npm registry https://registry.npmjs.org/promptfoo; GitHub repo metadata (e.g. https://github.com/mlflow/mlflow, https://github.com/UKGovernmentBEIS/inspect_ai, https://github.com/vibrantlabsai/ragas).
Inspect's PyPI author is "UK AI Security Institute" and its source link is still `UKGovernmentBEIS/inspect_ai` (https://pypi.org/project/inspect-ai/). A repo at `meridianlabs-ai/inspect_ai` exists but has 3 stars; I treat it as a fork.
Promptfoo acquisition: https://openai.com/index/openai-to-acquire-promptfoo/ and https://promptfoo.dev/blog/promptfoo-joining-openai.
Langfuse acquisition: https://clickhouse.com/blog/clickhouse-acquires-langfuse-open-source-llm-observability.
W&B acquisition: https://coreweave.com/blog/coreweave-completes-acquisition-of-weights-biases.

---

## 1. MLflow 3 GenAI

**(a) What it owns for us**
- **Judge definition.** `make_judge(name, instructions, model, feedback_value_type, inference_params, ...)`. Instructions use template variables `{{ inputs }}`, `{{ outputs }}`, `{{ expectations }}`, `{{ trace }}`, `{{ conversation }}`. Custom variables are not allowed. Source: https://raw.githubusercontent.com/mlflow/mlflow/master/mlflow/genai/judges/make_judge.py
- **Categorical verdicts.** `feedback_value_type` accepts `Literal[...]` of plain values. So `Literal["PASS", "FAIL", "UNKNOWN"]` is supported. Same source.
- **Per-persona criteria.** Because `{{ expectations }}` is a template variable, one judge can read each persona's own criterion from the record. We do not need one judge per persona.
- **Judge execution.** `mlflow.genai.evaluate(data, predict_fn, scorers)`. Data is a list of dicts with `inputs` and `expectations`. Each run is stored and shown in the UI. https://mlflow.org/docs/latest/genai/eval-monitor/
- **Results storage.** Default backend is a local SQLite file, `sqlite:///mlflow.db`, since 3.7.0. https://mlflow.org/docs/latest/self-hosting/architecture/backend-store/
- **Results UI.** `mlflow server` serves the UI at 127.0.0.1:5000. Same source.
- **Human labelling UI.** "Review Queues", added in 3.14.0 and marked experimental. Questions can be Pass/Fail, categorical, numeric or free text. A single Pass/Fail question submits on one click. Works on open-source MLflow with a local tracking store. Reviewer assignment needs a server with login; without it, all reviews go to one `default` user, which suits a single owner. https://mlflow.org/docs/latest/genai/assessments/review-queues/
  - Source check: the local SQL store defines the tables `review_queues`, `review_queue_users`, `review_queue_items` and `review_queue_label_schemas`, and has the methods to read and write them. So answers are stored in our local SQLite file. https://raw.githubusercontent.com/mlflow/mlflow/master/mlflow/store/tracking/dbmodels/models.py and https://raw.githubusercontent.com/mlflow/mlflow/master/mlflow/store/tracking/sqlalchemy_store.py
  - The review-queue classes are marked `@experimental(version="3.14.0")`. https://raw.githubusercontent.com/mlflow/mlflow/master/mlflow/genai/review_queues/review_queues.py
  - Do not confuse this with "labeling sessions" and the "Review App". Those are Databricks-only. The labeling store registers only `"databricks"` and raises "Labeling functionality is unavailable" otherwise. https://raw.githubusercontent.com/mlflow/mlflow/master/mlflow/genai/labeling/stores.py
  - Humans can also add feedback directly on a trace in the UI, or with `mlflow.log_feedback(..., source=AssessmentSource(source_type=HUMAN))`. https://mlflow.org/docs/latest/genai/assessments/feedback/
- **Judge tuning from human labels.** `judge.align(traces, optimizer)`. Optimizers: MemAlign (default), SIMBA and GEPA (both via DSPy), or custom. The human label's name must equal the judge's name. https://mlflow.org/docs/latest/genai/eval-monitor/scorers/llm-judge/alignment/
  - Source check: MemAlign imports `dspy` lazily and checks it is installed. Its defaults are `openai:/gpt-4o-mini` for reflection and `openai:/text-embedding-3-small` for embeddings off Databricks. It needs at least one trace with a human label. https://raw.githubusercontent.com/mlflow/mlflow/master/mlflow/genai/judges/optimizers/memalign/optimizer.py
  - The docs suggest at least 10 labelled traces and at least 30% of each class. Same alignment page.

**(b) What we still write**
- A loader that turns persona files into evaluation records.
- A `predict_fn` that calls our existing tour planner.
- One judge definition with instructions that demand a quoted excerpt as evidence.
- A pytest test that runs the evaluation and fails on any FAIL.
- Repeat runs. I found no built-in repeat option, so we loop (unverified that none exists).
- The agreement report. MLflow does not compute judge-vs-human agreement. The alignment page only shows a sample helper that compares accuracy. https://mlflow.org/docs/latest/genai/eval-monitor/scorers/llm-judge/alignment/

**(c) Providers.** Native URIs: `anthropic:/`, `openai:/`, `gemini:/`, `bedrock:/`, `ollama:/`, plus Azure, Mistral, Groq, OpenRouter, Together and others. LiteLLM is optional and covers anything else. vLLM is not named; it works through an OpenAI-compatible `base_url` or LiteLLM (unverified). https://mlflow.org/docs/latest/genai/eval-monitor/scorers/llm-judge/custom-judges/supported-models/
Judges on direct providers run from code, not from the UI. Same page.

**(d) Local storage and weight.** One Python package and one SQLite file. The UI is one local process. No Docker. Sources above.

**(e) pytest fit.** No pytest plugin. We call `mlflow.genai.evaluate` inside a test and assert on the results. A "Regression Testing and CI/CD" docs page exists but I could not read it (HTTP 403 on the evaluation page). Unverified.

**(f) Licence and paid gates.** Apache-2.0. The paid-only parts are on Databricks: labeling sessions, the Review App, and some review input types (`InputCategoricalList`, `InputTextList`). https://mlflow.org/docs/latest/genai/assessments/review-queues/

**(g) Telemetry.** On by default since 3.2.0. Turn off with `MLFLOW_DISABLE_TELEMETRY=true` or `DO_NOT_TRACK=true`. https://mlflow.org/docs/latest/community/usage-tracking/

**(h) Maturity.** Six releases between 23 September and 3 November 2026. ~28.8k GitHub stars. Review Queues are experimental. https://pypi.org/project/mlflow/#history

**(i) Verdict.** Best fit. It is the only local-first tool here that owns judge, storage, UI, labelling and judge tuning together.

---

## 2. DeepEval

**(a) What it owns**
- **Judge definition.** `GEval(name, criteria or evaluation_steps, evaluation_params, rubric, threshold, model, strict_mode)`. It returns a 0–1 score and a reason. It has no categorical output; `strict_mode` forces 1 or 0. https://deepeval.com/docs/metrics-llm-evals
- **Categorical judge.** `Classifier(name, labels=[Label(name, description)], model, include_reason=True)` picks one label and gives a reason. Setting `expected_labels` on the test case makes it pass or fail. This fits PASS/FAIL/UNKNOWN. Docs show it with `evaluate()`; I could not confirm it works with `assert_test`. https://deepeval.com/docs/classifiers-custom
- **DAG metric.** A fixed decision tree of LLM yes/no and multi-way judgements. Final outputs are scores only. https://deepeval.com/docs/metrics-dag
- `ConversationalGEval` exists for multi-turn chats; not needed here (from docs nav, not read in depth).
- **Execution and multi-run.** `assert_test()` inside pytest, run by `deepeval test run`. Flags: `-n` parallel, `-r N` repeat, `-c` cache, `-i` ignore errors. https://deepeval.com/docs/evaluation-flags-and-configs
- **Results storage.** JSON per run in `results_folder`, or SQLite with `DEEPEVAL_LOCAL_STORE=sqlite`. Same page.
- **CI gate.** A metric under threshold fails the test and the build. https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd

**(b) What we still write.** The human labelling UI, the alignment step and the agreement report. A results UI, unless the terminal viewer is enough.
- `deepeval inspect` is a local terminal viewer; `deepeval view` uploads to Confident AI. This comes from a third-party tutorial, not official docs. Unverified. https://wikidocs.net/379504

**(c) Providers.** Model classes include OpenAI, Azure, Anthropic, Gemini, Bedrock, Ollama, LiteLLM, a generic local model, DeepSeek, OpenRouter and others. https://raw.githubusercontent.com/confident-ai/deepeval/main/deepeval/models/__init__.py and https://deepeval.com/integrations/models/anthropic

**(d) Weight.** One Python package. No server.

**(e) pytest fit.** Strong. Plain `pytest` works, but the docs advise `deepeval test run`. https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd

**(f) Licence and paid gates.** Apache-2.0. Confident AI cloud: free tier has 2 seats and 5 test runs a week. Annotation queues start at Starter ($200/month). On-prem is Enterprise only. https://www.confident-ai.com/pricing

**(g) Telemetry.** On by default, sent to PostHog. Turn off with `DEEPEVAL_TELEMETRY_OPT_OUT=1`. https://deepeval.com/docs/data-privacy

**(h) Maturity.** Six releases from 15 October to 11 November 2026. ~19.5k stars. https://pypi.org/project/deepeval/#history

**(i) Verdict.** Best pytest story. But the owner's labelling and judge calibration sit behind the paid cloud, so we would build them ourselves.

---

## 3. Inspect AI

**(a) What it owns**
- **Task model.** `Task(dataset, solver, scorer)`. The solver can call our planner. https://inspect.aisi.org.uk/scorers.html
- **Judge.** `model_graded_qa(template, instructions, grade_pattern, include_history, partial_credit, model, model_role, reducer)`. `model` can be a list of graders. Their votes combine by `"majority"` (default) or `"mode"`. With custom instructions and `grade_pattern`, any letters can be parsed, so PASS/FAIL/UNKNOWN is possible. https://raw.githubusercontent.com/UKGovernmentBEIS/inspect_ai/main/src/inspect_ai/scorer/_model.py
- **Custom scorer.** `@scorer` returning `Score(value, answer, explanation, metadata)`. https://inspect.aisi.org.uk/scorers.html
- **Multi-run.** Epochs with reducers: mean (default), median, mode, max, `at_least_n`, `pass_at_k`. Mean turns NOANSWER into 0. https://inspect.aisi.org.uk/metrics.html
- **Results storage.** `.eval` log files in `./logs`. Read with `read_eval_log`. https://inspect.aisi.org.uk/eval-logs.html
- **Results UI.** `inspect view` shows each sample, the transcript, the score and the judge's explanation. `inspect view bundle` makes a static site. https://inspect.aisi.org.uk/log-viewer.html
- **Re-scoring.** `inspect score` re-scores a log, for example with a different judge. https://inspect.aisi.org.uk/scoring-workflow.html

**(b) What we still write.** The owner's labelling UI. `edit_score()` changes a score in Python and records author and reason in the score history, but the viewer does not offer editing (unverified that it never does). https://inspect.aisi.org.uk/eval-logs.html
Also the alignment step and agreement report. Also a pytest bridge, since Inspect has its own runner.

**(c) Providers.** `anthropic/`, `openai/`, `google/`, `mistral/`, `grok/`, `bedrock/`, `azureai/`, `ollama/`, `vllm/`, `hf/`, `llama-cpp-python/`, `openai-api/` for any OpenAI-compatible server, `openrouter/`, and more. https://inspect.aisi.org.uk/providers.html

**(d) Weight.** One Python package and log files. No server.

**(e) pytest fit.** Weak. Call `inspect_ai.eval()` from a test and assert on the returned log (no official pytest plugin found; unverified).

**(f) Licence.** MIT. No paid tier.

**(g) Telemetry.** No telemetry found in the docs I read. Unverified.

**(h) Maturity.** Six releases from 25 October to 14 November 2026. ~3.3k stars. Run by a government lab. https://pypi.org/project/inspect-ai/#history

**(i) Verdict.** Best runner for repeated runs and multiple graders. No labelling UI and no judge tuning. Good fallback if we drop MLflow.

---

## 4. Promptfoo

**(a) What it owns**
- **Judge.** `llm-rubric` returns `{reason, score, pass}`. Override the grader per assertion, per test or with `--grader`. Custom grading prompt via `rubricPrompt`. https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/llm-rubric/
- `g-eval` also exists, scored 0–1 with threshold 0.7. https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/g-eval/
- **Python asserts.** `file://assert.py` returns bool, score or `{pass, score, reason}`. https://www.promptfoo.dev/docs/configuration/expected-outputs/python/
- **Multi-run.** `--repeat N`. **CI gate.** Exit code 100 when a test fails or the pass rate is below `PROMPTFOO_PASS_RATE_THRESHOLD`. https://www.promptfoo.dev/docs/usage/command-line/
- **Storage.** SQLite at `~/.promptfoo/promptfoo.db`. https://www.promptfoo.dev/docs/usage/self-hosting/
- **Results UI with human labels.** `promptfoo view` lets a person mark each result pass or fail, set a score or add a comment. These persist and are exported. https://www.promptfoo.dev/docs/usage/web-ui/

**(b) What we still write.** The YAML config bridge from personas, a Python provider that calls our planner, and the agreement report. No judge tuning.

**(c) Providers.** Anthropic, Google and OpenAI graders are documented. Ollama is not named on the rubric page. Promptfoo says it will keep supporting many providers after the OpenAI deal. https://promptfoo.dev/blog/promptfoo-joining-openai

**(d) Weight.** The real tool is the npm package and needs Node 22.22 or later. The PyPI `promptfoo` 0.2.0 package is only a wrapper. https://www.promptfoo.dev/docs/usage/self-hosting/

**(e) pytest fit.** None. It is its own CLI.

**(f) Licence.** MIT. Sharing goes through Promptfoo Cloud or a self-hosted server.

**(g) Telemetry.** On by default. Turn off with `PROMPTFOO_DISABLE_TELEMETRY=1`. If you are logged in, it sends your email. https://www.promptfoo.dev/docs/configuration/telemetry/

**(h) Maturity.** Very active, ~27k stars. OpenAI agreed to buy it in March 2026.

**(i) Verdict.** Its viewer is a real labelling UI. But it adds Node, sits outside pytest, has no judge tuning, and is owned by a judge vendor.

---

## 5. Langfuse

**(a) What it owns**
- **Judge.** Managed LLM-as-a-judge evaluators run on the Langfuse worker. Scores can be numeric, categorical or boolean, with reasoning. https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge
- **Datasets and experiments.** `langfuse.run_experiment(name, data, task, evaluators)` works with local lists or hosted datasets. Results go to the server. https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk
- **Labelling UI.** Annotation queues with categorical and boolean score configs and keyboard shortcuts. https://langfuse.com/docs/evaluation/evaluation-methods/annotation-queues
- **Agreement metrics.** Score Analytics computes overall agreement, Cohen's kappa, F1 and a confusion matrix between two score sources. It is beta. **I could not confirm it runs on self-hosted.** https://langfuse.com/docs/evaluation/evaluation-methods/score-analytics

**(b) What we still write.** Pytest bridge, persona upload, the catch-rate and pass-rate split (Score Analytics gives a confusion matrix, which would cover it if it works self-hosted). No judge tuning.

**(c) Providers.** Through "LLM connections". Docs name GPT, Claude and Gemini. The judge must support structured output. Ollama and vLLM not named. Same judge page.

**(d) Weight.** Heavy. Web, worker, Postgres, ClickHouse, Redis or Valkey, and S3 storage (MinIO in compose). Recommended VM: 4 cores, 16 GiB. https://langfuse.com/self-hosting and https://langfuse.com/self-hosting/deployment/docker-compose

**(e) pytest fit.** None documented.

**(f) Licence.** All product features, including evaluations, experiments and annotation, are MIT. SCIM, audit logs and retention policies need a licence. https://langfuse.com/docs/open-source

**(g) Telemetry.** Self-hosted sends aggregate counts to PostHog every 12 hours. Turn off with `TELEMETRY_ENABLED=false`. https://langfuse.com/self-hosting/security/telemetry

**(h) Maturity.** Very active, ~37k stars. Owned by ClickHouse since 2026, which says the MIT licence stays.

**(i) Verdict.** Strong features, too heavy for a single-owner local loop.

---

## 6. Arize Phoenix

**(a) What it owns.** Tracing, datasets, experiments, LLM judges whose labels map to scores with explanations, and annotation queues. https://arize.com/docs/phoenix/evaluation/llm-evals
Annotation queue docs found were mostly for Arize AX, the paid product. Whether Phoenix has the same queue UI is unverified.

**(b) What we still write.** Pytest bridge, alignment and agreement report.

**(c) Providers.** The evals `LLM(provider, model, client, ...)` class resolves providers at runtime. Docs name OpenAI, LiteLLM and LangChain adapters. The full list is unverified. https://raw.githubusercontent.com/Arize-ai/phoenix/main/packages/phoenix-evals/src/phoenix/evals/llm/llm.py

**(d) Weight.** One container. SQLite by default in `~/.phoenix/`; Postgres for teams. https://arize.com/docs/phoenix/self-hosting/architecture

**(e) pytest fit.** None documented.

**(f) Licence.** Elastic License 2.0. You may not offer it to others as a hosted service. Internal use is fine. No feature gates on self-host. https://raw.githubusercontent.com/Arize-ai/phoenix/main/LICENSE and https://arize.com/docs/phoenix/self-hosting

**(g) Telemetry.** UI web analytics on by default in current docs; an older page said off. Turn off with `PHOENIX_TELEMETRY_ENABLED=false`. https://arize.com/docs/phoenix/self-hosting/security/privacy and https://pypi.org/p/arize-phoenix

**(h) Maturity.** Very active, ~12k stars.

**(i) Verdict.** Fine tool. Not open source by OSI terms, and no judge tuning. MLflow covers the same ground.

---

## 7. Opik

**(a) What it owns.** G-Eval and custom LLM judges, LLM juries, test suites with natural-language assertions, annotation queues with categorical and numeric scores, and judge alignment against human scores. https://www.comet.com/docs/opik/llms.txt and https://www.comet.com/docs/opik/evaluation/annotation_queues.md
- Alignment uses `MetaPromptOptimizer` (or GEPA) from the `opik-optimizer` package. It reports agreement before and after as 1 minus the score gap. It trains and measures on the same items. https://www.comet.com/docs/opik/evaluation/judge_alignment.md
- Pytest: `@llm_unit()` logs each test as an experiment item. I saw search snippets only, not the page. Unverified. https://www.comet.com/docs/opik/python-sdk-reference/testing/llm_unit.html

**(b) What we still write.** Persona upload and the catch-rate split.

**(c) Providers.** Custom models supported (https://www.comet.com/docs/opik/evaluation/metrics/custom_model.md). The alignment guide needs an OpenAI key. Full list not checked.

**(d) Weight.** Heavy. Java backend, Python backend, React frontend on Nginx, ClickHouse with Zookeeper, MySQL, Redis and MinIO. Local install is "not production-ready". https://www.comet.com/docs/opik/self-host/architecture.md and https://www.comet.com/docs/opik/self-host/overview

**(e) pytest fit.** Decorator exists (unverified detail).

**(f) Licence.** Apache-2.0. Self-hosted has all features except user management. Whether alignment works self-hosted is unverified.

**(g) Telemetry.** On by default. Turn off with `OPIK_USAGE_REPORT_ENABLED=false`. https://comet.com/docs/opik/self-host/configure/anonymous_usage_statistics

**(h) Maturity.** Very active, near-daily releases, ~23k stars.

**(i) Verdict.** Feature set matches ours closely. The seven-service stack is the cost.

---

## 8. Ragas

**(a) What it owns.** Judge metrics only.
- `AspectCritic(name, definition, llm)` gives a yes/no verdict by majority vote over several LLM calls.
- `RubricsScore` scores against a 1–5 rubric.
- `InstanceRubrics` reads a different rubric from each sample. That matches per-persona criteria.
- `DiscreteMetric(allowed_values=...)` allows custom categories such as PASS/FAIL/UNKNOWN.
- https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/general_purpose/
- Whether these return a written reason is not stated on that page. Unverified.

**(b) What we still write.** Everything else: runner, storage, UI, labelling, agreement.

**(c) Providers.** `llm_factory(model, provider=...)` handles openai, anthropic, google/gemini, litellm, groq, mistral, bedrock and others through Instructor. https://raw.githubusercontent.com/vibrantlabsai/ragas/main/src/ragas/llms/base.py

**(d) Weight.** One package. **(e) pytest.** Call from a test. **(f) Licence.** Apache-2.0.

**(g) Telemetry.** Anonymous usage data; turn off with `RAGAS_DO_NOT_TRACK=true`. https://raw.githubusercontent.com/vibrantlabsai/ragas/main/README.md

**(h) Maturity.** Release gap from January to November 2026, then two releases in two days. Repo changed owner. https://pypi.org/project/ragas/#history

**(i) Verdict.** Not needed. MLflow's `{{ expectations }}` already gives per-persona rubrics.

---

## 9. openevals and agentevals (LangChain)

- `create_llm_as_judge(prompt, model="provider:model" or judge=client, choices, continuous, use_reasoning, feedback_key, output_schema, few_shot_examples)`. Returns `{key, score, comment}`. A custom `output_schema` can hold a categorical verdict and a quote. https://raw.githubusercontent.com/langchain-ai/openevals/main/README.md
- Providers come from LangChain's `init_chat_model`: OpenAI, Anthropic, Gemini and anything LangChain supports. Same README.
- Works with LangSmith's pytest runner, but LangSmith is not needed to call a judge. Same README.
- MIT. No storage, UI or labelling. Telemetry not mentioned.
- agentevals scores agent tool-call trajectories. Last release July 2025. Not relevant.
- **Verdict.** A judge helper only. MLflow's `make_judge` already does this.

## 10. Braintrust autoevals

- `LLMClassifier(prompt_template, choice_scores, use_cot)` maps labels to scores with chain-of-thought. Also Factuality, ClosedQA and others. https://raw.githubusercontent.com/braintrustdata/autoevals/main/README.md
- Uses an OpenAI-compatible client. Without a base URL it falls back to the Braintrust gateway, which needs a Braintrust key. Gemini not mentioned. Same README.
- MIT (https://github.com/braintrustdata/autoevals). Library only.
- **Verdict.** Not needed.

## 11. Evidently

- LLM judges via `BinaryClassificationPromptTemplate` and `MulticlassClassificationPromptTemplate`. `include_reasoning` is on by default. The `uncertainty` option returns `unknown` when the input is not enough to decide. **This is the only built-in UNKNOWN verdict I found.** https://docs.evidentlyai.com/metrics/customize_llm_judge
- Providers through LiteLLM: OpenAI, Anthropic, Gemini, Mistral, Ollama. Same page.
- `evidently ui` serves a local UI over a folder, SQLite, Postgres or S3 workspace. Evidently Cloud is no longer sold as SaaS. https://docs.evidentlyai.com/docs/setup/self-hosting
- No labelling UI or judge tuning found. Telemetry not checked. Unverified.
- Last release 11 September 2026; slower cadence.
- **Verdict.** Nice judge templates, but nothing MLflow lacks.

## 12. W&B Weave

- The client is Apache-2.0, but it needs a W&B account and API key. Self-hosting needs a Weave-enabled licence and ClickHouse. https://raw.githubusercontent.com/wandb/weave/master/README.md and https://docs.wandb.ai/weave/guides/platform/weave-self-managed
- **Verdict.** Ruled out. Not local-first.

---

## Comparison table

Key: Y = owns it. P = partly. N = we write it. ? = unverified.

| | MLflow 3 | DeepEval | Inspect | Promptfoo | Langfuse | Phoenix | Opik | Ragas / openevals / autoevals |
|---|---|---|---|---|---|---|---|---|
| Judge definition | Y | Y | Y | Y | Y | Y | Y | Y |
| PASS/FAIL/UNKNOWN natively | Y (`Literal`) | Y (`Classifier`) | P (custom pattern) | N (pass + score) | Y (categorical) | Y (labels) | ? | P |
| Per-persona criteria | Y (`expectations`) | Y | Y (`target`) | Y (vars) | P | P | P | Y (`InstanceRubrics`) |
| Judge execution | Y | Y | Y | Y | Y (worker) | Y | Y | Y |
| Multi-run | N (loop) | Y (`-r`) | Y (epochs) | Y (`--repeat`) | N | ? | ? | N |
| Persona dataset | P | P | Y | P (YAML) | Y | Y | Y | N |
| Human labelling UI | Y (Review Queues, experimental) | N (paid cloud) | N | Y (viewer overrides) | Y | ? (AX docs) | Y | N |
| Judge-vs-human metrics | N | N | N | N | Y? (self-host unverified) | N | P (one score) | N |
| Judge tuning from labels | Y (`align`) | N | N | N | N | N | Y | N |
| Local storage | SQLite file | JSON / SQLite | log files | SQLite | 4 data services | SQLite | 5 data services | none |
| Results UI | Y | P (terminal?) | Y | Y | Y | Y | Y | N |
| CI gate | N (we assert) | Y | P | Y (exit 100) | N | N | P | N |
| pytest fit | P | Y | P | N | N | N | P? | P |
| Anthropic / OpenAI / Gemini | Y/Y/Y | Y/Y/Y | Y/Y/Y | Y/Y/Y | Y/Y/Y | ?/Y/? | ? | Y/Y/Y |
| Ollama / vLLM / LiteLLM | Y/?/Y | Y/?/Y | Y/Y/via OpenAI-compatible | ? | ? | ?/?/Y | ? | varies |
| Self-host weight | 1 process | none | none | Node | heavy | 1 container | heavy | none |
| Licence | Apache-2.0 | Apache-2.0 | MIT | MIT | MIT + ee | ELv2 | Apache-2.0 | Apache / MIT |
| Telemetry default | on | on | ? | on | on | on | on | on / ? / ? |

---

## Recommendation

**Who owns which job**

| Job | Owner |
|---|---|
| Persona files and loading | Our code (a small loader) |
| Producing the tour | Our existing planner, called from a `predict_fn` |
| Deterministic criteria | pytest assertions |
| Narration judge definition | MLflow `make_judge`, one generic judge |
| Judge execution | `mlflow.genai.evaluate` |
| Judge provider choice | MLflow model URI, e.g. `anthropic:/…`, `openai:/…`, `gemini:/…`, `ollama:/…` |
| Repeated runs | A loop in our pytest wrapper |
| Results storage | MLflow's local SQLite file |
| Results UI | `mlflow server` (local) |
| Owner's labels | MLflow Review Queues, one categorical question with exactly `PASS`, `FAIL`, `UNKNOWN`, named the same as the judge |
| Judge tuning | `judge.align()` with MemAlign, run by hand when labels change |
| Catch-rate and pass-rate | Our code (a small report) |
| CI gate | pytest exit code |

**The minimum code we write**

1. **Persona loader.** Read each persona file. Emit one record per narration criterion: `{"inputs": {persona id, trip request}, "expectations": {"criterion": text}}`.
2. **`predict_fn`.** Call the existing planner and return the narration text. It must import the planner used by the app and workbench, not a copy. **Build each tour once per persona per repeat**, and reuse it for all that persona's criteria (cache the result by persona id and repeat number). Otherwise a persona with five criteria gets five different tours, at five times the cost, and each criterion is judged on a different narration. Caching inside `predict_fn` keeps one trace per row, which Review Queues and `align()` need. (The docs say rows can also carry outputs made beforehand; I did not confirm those rows still produce traces Review Queues can use.)
3. **One judge.** `make_judge(name="narration_criterion", instructions=..., feedback_value_type=Literal["PASS","FAIL","UNKNOWN"], model=<URI>)`. The instructions say: judge `{{ outputs }}` against `{{ expectations }}`, quote the sentence that decides it, answer UNKNOWN when the narration does not address the criterion. MLflow returns the rationale with each verdict.
4. **pytest wrapper.** Run deterministic asserts. Then run `mlflow.genai.evaluate` N times and fail the test on any FAIL.
5. **Agreement report.** Pull traces with both a human label and a judge verdict for `narration_criterion`. Count:
   - **Catch-rate** = judge FAIL ÷ human FAIL.
   - **Pass-rate** = judge PASS ÷ human PASS.
   - A judge UNKNOWN on a human FAIL counts as **not caught**. A judge UNKNOWN on a human PASS counts as **not passed**. Report UNKNOWN counts on their own line too.
   - Drop items the human marked UNKNOWN from both rates, and report how many were dropped.

That is about 150–250 lines in all. No new UI, store or judge framework.

**Setup steps, not code**
- Add `mlflow` and `dspy` through `uv.lock` and `make sync`.
- Set `MLFLOW_DISABLE_TELEMETRY=true` in the test environment.
- Pass a non-OpenAI reflection and embedding model to MemAlign if we want to avoid OpenAI. MemAlign defaults to OpenAI off Databricks.

**Fallback.** If Review Queues prove too unstable, keep MLflow for the judge and storage. Let the owner label in our workbench, which writes `mlflow.log_feedback` with a HUMAN source. `align()` and the report keep working.

---

## Risks

- **Review Queues are experimental** (since 3.14.0) and may change. https://mlflow.org/docs/latest/genai/assessments/review-queues/
- **Two similar MLflow features.** Labeling sessions and the Review App are Databricks-only. Only Review Queues work locally. Copying a Databricks tutorial will fail with "Labeling functionality is unavailable".
- **The human label name must equal the judge name**, or `align()` raises.
- **The label values must match the judge's values exactly.** The built-in Pass/Fail question type has only two values, so use a categorical question with `PASS`, `FAIL`, `UNKNOWN`, spelled as in the judge. `Pass` and `PASS` will not match.
- **`align()` pulls in DSPy** and calls LLMs for reflection and embeddings. Cost and run time are not documented.
- **Alignment can overfit.** Opik's docs say its optimizer measures on the items it trained on. MLflow does not say. Keep some labelled items back to measure the tuned judge.
- **No local tool reports catch-rate and pass-rate.** We own that code. Langfuse's Score Analytics is the nearest, and I could not confirm it works self-hosted.
- **Telemetry is on by default** in MLflow, DeepEval, Promptfoo, Langfuse, Phoenix, Opik and Ragas. Each needs its opt-out variable.
- **Ownership changes.** OpenAI agreed to buy Promptfoo, and ClickHouse bought Langfuse. Both promise to keep their licences; neither promise binds future versions.
- **Judge on the same model family as the writer.** A Claude judge grading Claude narration may share its blind spots. MLflow's provider URIs make it cheap to try another judge.

## Not verified

- MLflow: no built-in repeat option; the CI/CD docs page; vLLM by name.
- DeepEval: `Classifier` with `assert_test`; whether `deepeval view` needs the cloud.
- Inspect: telemetry; score editing in the viewer; a pytest plugin.
- Phoenix: full judge provider list; annotation queues in open-source Phoenix.
- Opik: `llm_unit` detail; alignment when self-hosted; provider list.
- Langfuse: Score Analytics on self-host; Ollama or vLLM as judge.
- Ragas: whether rubric metrics return a reason.
- Evidently: telemetry; labelling.
