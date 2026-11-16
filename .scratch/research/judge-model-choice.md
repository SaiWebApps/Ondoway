# Choosing a judge model for tour narration

Researched 15 November 2026. Every claim has a link. Claims I could not confirm on a primary
source are marked **(unverified)**. Prices are US dollars per million tokens (input / output).

## The answer first

- No published benchmark tests the newest OpenAI or Google models (GPT-6.x, Gemini 4) as judges.
  The newest careful study stops one generation earlier, at GPT-5.4, Gemini 3.1 Pro and Claude
  Opus 4.6. Benchmarks can narrow the field. Only our own experiment can pick the judge.
- Every public judge benchmark compares two answers or checks one fact. Ondoway asks something
  else: a PASS, FAIL or UNKNOWN verdict on one long text, one criterion at a time, with quotes. The
  closest published study is one on rubric-style yes/no verdicts (Pombal et al., below).
- Self-preference is real, and it shows up as leniency. A judge most often lets its own family's
  failures through as passes. So the number that matters most is how many of the human FAILs the
  judge catches.
- Shortlist to test: **`gpt-6.1-terra`** (OpenAI), **`gemini-4-hydrogen`** and **`gemini-4-argon`**
  (Google), and **`Qwen/Qwen3.8-27B`** (open weights). We also score a 2-of-3 vote of Terra,
  Hydrogen and Qwen. It reuses the same runs, so it costs nothing extra.
- Codex is not a separate option. The only Codex-specific model, `gpt-5.3-codex`, is deprecated and
  built for agentic coding. Codex itself now runs the general GPT-6 models.
- Antigravity is a tool, not a model. It is Google's agent app, CLI and IDE, and it lets you pick a
  model. Its picker includes Claude Opus 5.5, so choosing Antigravity does not by itself give a
  non-Claude judge.
- Every judge, from any family, tends to go easy on stronger writers. Ondoway's writer is a
  frontier model. A different family alone does not fix that. Planting failures in the test set,
  and gating on catch-rate first, does.
- The experiment costs roughly $9 to $135 per candidate at moderate thinking (section 5). The whole
  shortlist costs about $225.

---

## 1. Candidate models, November 2026

### 1a. OpenAI

Source for ids, context and prices: the OpenAI models page
(https://developers.openai.com/api/docs/models) and pricing page
(https://developers.openai.com/api/docs/pricing).

| Model id | Context | Max output | Price (standard) | Batch / Flex | Notes |
|---|---|---|---|---|---|
| `gpt-6-astra` | 1.05M | 128K | $10 / $50 | $5 / $25 | The "most capable" model. No `none` reasoning setting. |
| `gpt-6.1-sol` | 1.05M | 128K | $2 / $10 | $1 / $5 | Middle tier. No `none` reasoning setting. |
| `gpt-6.1-terra` | 1.05M (922K max input) | 128K | $1 / $5 (cached input $0.05) | $0.50 / $2.50 | OpenAI suggests it for "natural writing". Reasoning settings run from `none` to `max`. |
| `gpt-6-luna` | 1.05M | 128K | $0.10 / $0.50 | $0.05 / $0.25 | Cheap, high-volume work. |
| `gpt-oss-120b` (open weights) | 131K (OpenRouter listing) | — | about $0.15 / $0.60 through hosts | — | 117B total, 5.1B active. Apache 2.0. Fits on one 80 GB GPU. |

- **Structured output.** All GPT-6 models support Structured Outputs with a strict JSON schema
  (https://developers.openai.com/api/docs/guides/structured-outputs). The schema must list every
  field as required and set `additionalProperties: false`.
- **Terra details** come from its model page (https://developers.openai.com/api/docs/models/gpt-6.1-terra):
  knowledge cutoff 1 June 2026. Prompts over 272K tokens cost 2× on input and 1.5× on output.
- **The GPT-6 guide recommends Terra for writing**
  (https://developers.openai.com/api/docs/guides/latest-model). It names no model for evaluation.
- **Codex.** The Codex models page (https://learn.chatgpt.com/docs/models) lists only the general
  models `gpt-6-astra`, `gpt-6.1-sol`, `gpt-6.1-terra` and `gpt-6-luna`. The last Codex-specific
  model, `gpt-5.3-codex`, is "the most capable agentic coding model to date". It is marked
  deprecated, it works only through the Responses API, and it has a 400K context
  (https://developers.openai.com/api/docs/models/gpt-5.3-codex). It is tuned for code. Nothing
  suggests it judges prose better than the general models, so leave it out.
- **gpt-oss-120b** (https://huggingface.co/openai/gpt-oss-120b) is OpenAI's own open-weight model.
  It is still the August 2025 release, and no newer version is listed. As an OpenAI-family model,
  it does not count as a third family.

### 1b. Google Gemini

Sources: the models list (https://aistudio.google.com/docs/models), each model's page, and the
pricing page (https://aistudio.google.com/docs/pricing).

| Model id | Status | Context | Price (standard) | Batch / Flex | Notes |
|---|---|---|---|---|---|
| `gemini-4-carbon` | Stable, Nov 2026 | 1,048,576 shared in/out; 65K output by default | $2 / $10 until 30 Nov; **$4 / $20 from 1 Dec 2026** | $1 / $5, then $2 / $10 | "Most capable reasoning model" (https://aistudio.google.com/docs/models/gemini-4-carbon). No free tier. |
| `gemini-4-argon` | Stable, Oct 2026 | same | $2 / $10 until 30 Nov; **$4 / $20 from 1 Dec 2026** | $1 / $5, then $2 / $10 | Faster and uses fewer tokens than Carbon (https://aistudio.google.com/docs/models/gemini-4-argon). Thinking levels: low, medium, high. |
| `gemini-4-hydrogen` | Stable, Nov 2026 | same | $1.50 / $7.50 (no price change listed) | $0.75 / $3.75 | For "everyday" work (https://aistudio.google.com/docs/models/gemini-4-hydrogen). Thinking levels go up to `xhigh`. |
| `gemini-3.8-flash` | Stable | not stated | $0.75 / $3.75 until 31 Dec; **$1.50 / $7.50 from 1 Jan 2027** | half | |
| `gemini-3.1-pro-preview` | Preview | not stated | $2 / $12 (≤200K prompt) | $1 / $6 | This is the strongest Gemini judge with published evidence (section 2). |

- **Structured output.** The Gemini 4 model pages list structured outputs as supported. The guide
  (https://aistudio.google.com/docs/structured-output) supports `enum`, `required` and
  `additionalProperties`. It silently drops `minLength`, `maxLength` and a few other keywords, so
  validate those in our own code.
- **Deprecations.** Gemini 2.5 Pro and 2.5 Flash are redirected to newer models on 1 December
  2026 (models page).

**Antigravity.** The Antigravity docs (https://antigravity.google/docs/models) describe a product for
working with AI agents in an app, CLI or IDE. It is not a model. Its model picker lists:

- Gemini 4 Carbon, Argon and Hydrogen
- Gemini 3.8, 3.7 and 3.6 Flash, and Gemini 3.1 Pro
- Claude Sonnet 5.5 and Opus 5.5, and Claude Sonnet 4.6 and Opus 4.6
- GPT-OSS 120B

The Claude 4.6 models and GPT-OSS 120B are removed on 18 November 2026. There is also an API
agent, `antigravity-preview-09-2026`. It is a preview "managed agent" that runs in a Google
sandbox, and its page does not say which Gemini model it uses
(https://aistudio.google.com/docs/models/antigravity-preview-09-2026). For judging, call the
Gemini model directly. An agent sandbox adds cost and variation and brings no benefit.

### 1c. Open-weight models

Newest releases come from the Hugging Face model hub (queried 15 Nov 2026). Hosted prices come
from OpenRouter's public model list (https://openrouter.ai/api/v1/models). These are
**OpenRouter listings, not the makers' official prices**.

| Model | Size | Context | Licence | Runs locally? | Hosted price (OpenRouter) |
|---|---|---|---|---|---|
| `Qwen/Qwen3.8-27B` (https://huggingface.co/Qwen/Qwen3.8-27B) | 27B dense | 262K native, up to 1M | Apache 2.0 | Yes. About 54 GB in BF16, and much less once quantised (my arithmetic). Thinking is on by default and can be turned off. | $0.15 / $1.875 |
| `google/gemma-4-31B-it` (https://huggingface.co/google/gemma-4-31B-it) | 31B dense | 256K | Apache 2.0 | Yes. 70 GB in BF16, 17.5 GB at 4-bit (https://ai.google.dev/gemma/docs/core) | $0.20 / $0.40 |
| `deepseek-ai/DeepSeek-V4.1-Flash` (https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) | 552–763B total MoE | 1M | MIT | No, it needs a multi-GPU server | DeepSeek's own API, `deepseek-flash`: $0.15 / $0.60 off-peak (https://api-docs.deepseek.com/quick_start/pricing) |
| `mistralai/Mistral-Large-4-1T-A52B` (https://huggingface.co/mistralai/Mistral-Large-4-1T-A52B) | about 1T total, about 49B active | 1M | Mistral open-weight licence (a modified MIT). Selling it as a hosted service needs a commercial licence. | No, it needs 8 GPUs | $0.70 / $2.10. The API id is `mistral-large-4`. |
| `openai/gpt-oss-120b` | 117B MoE | 131K | Apache 2.0 | One 80 GB GPU | $0.15 / $0.60 |
| `meta-llama/Llama-4-*` | — | — | Llama licence | — | Meta has published no new Llama on Hugging Face since April 2025. Treat it as stale. |

Two cautions apply.

- **Family overlap.** Gemma is Google's model family and gpt-oss is OpenAI's. Put either of them in
  a panel beside Gemini or GPT, and two of the three votes come from one family. The open member of
  a panel should come from a third family. Qwen, DeepSeek or Mistral qualify.
- **DeepSeek's own API stores data in China.** Its privacy policy says it collects, processes and
  stores personal data in the People's Republic of China
  (https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html). Use another host or our
  own hardware if we pick it.

### 1d. Purpose-built judge models

These are all old, and the benchmarks rank them below general models.

| Judge | Base model, date | Evidence |
|---|---|---|
| Prometheus 2, 7B and 8x7B (https://huggingface.co/prometheus-eval/prometheus-8x7b-v2.0) | Mistral 7B, May 2024 | On JudgeBench it scores 34.9% and 40.3%, below the 50% a coin flip would get (https://arxiv.org/html/2410.12784v2) |
| Skywork-Critic-Llama-3.1-70B | Llama 3.1, 2024 | 57.4% on JudgeBench. In 2026 it scored the lowest agreement of any judge in the JudgeArena study (κ 0.08) (https://arxiv.org/html/2608.02620v1) |
| Selene 1 Mini (https://huggingface.co/AtlaAI/Selene-1-Mini-Llama-3.1-8B) | Llama 3.1 8B, Jan 2025 | Claims to beat GPT-4o on RewardBench. A third-party directory says Atla's API was archived in 2025 (https://providers.apievangelist.com/providers/atla/) **(unverified)**. |
| Flow-Judge v0.1 (https://huggingface.co/flowaicom/Flow-Judge-v0.1) | Sept 2024 | No newer version on the Hugging Face hub |
| Skywork-Reward-V2-Llama-3.1-8B | 2025 | Tops RewardBench 2 at 84.1, but it outputs a score, not quotes and reasons. It cannot fill our JSON. |

I found no 2026 purpose-built judge with published results. Leave this group out.

---

## 2. What the judge benchmarks say

| Benchmark | Newest models tested | What it measures | Who leads |
|---|---|---|---|
| **"Reliability without Validity"**, June 2026 (https://arxiv.org/html/2606.19544v1) | GPT-5.4, Gemini 3.1 Pro, Claude Opus 4.6, DeepSeek V3.2, Kimi K2.5, gpt-oss-120B and 15 others | Agreement with humans as Cohen's κ (agreement corrected for chance) on three sets: MT-Bench (preference), JudgeBench (correctness) and RewardBench | See the table below |
| JudgeBench (https://arxiv.org/abs/2410.12784), ICLR 2025 | GPT-4o, fine-tuned judges | Picking the correct answer in hard pairs on knowledge, reasoning, maths and code. 50% is chance. | GPT-4o scores 50.9–56.6%. Most fine-tuned judges score at or below chance. |
| RewardBench 2 (https://arxiv.org/html/2506.01937v2). Results data last updated 11 Dec 2025 (https://huggingface.co/datasets/allenai/reward-bench-2-results) | Gemini 2.5 Pro, Claude Opus 4, GPT-4.1 | Picking the best of four answers. 25% is chance. | Among general models used as judges: Gemini 2.5 Pro 79.5, Claude Opus 4 76.5, GPT-4.1 72.3 (my averages from the results files). No 2026 models. |
| JudgeArena, July 2026 (https://arxiv.org/html/2608.02620v1) | gemma-4-31b-it, gemini-3.1-flash, qwen3.5 series, gpt-5.4-nano | Agreement with crowd preference votes from LMArena | Gemma 4 31B and Gemini 3.1 Flash tie at κ 0.17. The authors' own re-labelling agreed with the crowd only at about 0.2. Taste-based preference has a low ceiling. |
| Atla Judge Arena (https://huggingface.co/blog/arena-atla), Nov 2024 | GPT-4 Turbo, Llama 3.1 | Human votes on judges | Stale. I could not confirm that it still updates. |
| LLMBar (https://arxiv.org/html/2310.07641v2), 2023 | GPT-4 | Instruction following, with traps where the wrong answer sounds better | Humans agree 94%. GPT-4 scores 73% on the traps, and 83% with explicit rules, metrics and a reference answer. Chain-of-thought alone did not help. |
| "Judging the Judges" (https://arxiv.org/html/2406.12624), v6 Aug 2025 | GPT-4 Turbo, Llama 3.1 70B | Grading short trivia answers against a key | Only the largest models came close to humans. Most judges were **too lenient**. Plain percent agreement hid large differences that a chance-corrected score revealed. |
| LitBench (https://arxiv.org/abs/2507.00769), 2025 | Claude 3.7 Sonnet | Human preferences between stories | The best off-the-shelf judge agreed 73%. A trained reward model agreed 78%. |

**Key figures from the June 2026 study.** I checked these against the paper's HTML tables. Each cell
gives κ with the judge's rank in brackets.

| Judge | Preference (MT-Bench) | Correctness (JudgeBench) | RewardBench |
|---|---|---|---|
| Gemini 3.1 Pro | 0.511 (1) | 0.841 (2) | 0.898 (1) |
| Claude Opus 4.6 | 0.489 (2) | 0.875 (1) | 0.879 (4) |
| DeepSeek V3.2 | 0.486 (3) | 0.545 (13) | 0.826 (12) |
| Kimi K2.5 | 0.461 (6) | 0.720 (4) | 0.873 (7) |
| GPT-5.4 | 0.457 (7) | 0.606 (9) | 0.879 (5) |
| gpt-oss-120B | 0.441 (12) | 0.687 (6) | 0.880 (3) |

What this means for us:

- On preference tasks, the top judges sit within a few points of each other. On correctness tasks
  they spread out widely, from κ 0.27 to 0.88. Strong models separate most where there is a right
  answer.
- Most of our criteria can be checked against the text. "Is each French word explained where it
  first appears?" and "Does any sentence name three or more people?" are examples. Those behave
  more like correctness tasks. Two criteria lean on taste or world knowledge: "works aloud for an 8-
  and a 5-year-old", and "tells a resident something new", which needs a sense of what guidebooks
  lead with. Expect lower agreement on those two.
- Google's judges led in 2026, but the evidence is for Gemini 3.1 Pro, not Gemini 4. OpenAI's
  GPT-5.4 was mid-pack on correctness. No figures exist yet for GPT-6.x or Gemini 4.
- **How stale is the evidence?** Every benchmark above tested models at least one generation older
  than our candidates. All of them are pairwise or preference tasks, and none uses a rubric over a
  long document.

---

## 3. Self-preference and judge panels

| Study | Finding |
|---|---|
| Panickssery et al. 2024 (https://arxiv.org/html/2404.13076) | GPT-4 recognised its own summaries 73.5% of the time. The more a model recognised its own text, the more it preferred it, a linear link. The study did not control for whether the model's text really was better. |
| Wataoka et al. 2024 (https://arxiv.org/html/2410.21819v1) | GPT-4 showed clear self-preference (bias score 0.520). The bias tracks familiarity: judges rate text that looks likely to them higher than humans do, whoever wrote it. The paper did not test same-family bias. |
| Pombal et al., Apr 2026, rubric verdicts (https://arxiv.org/html/2604.06996v1) | This is closest to our setup: binary pass/fail rubrics. On objective rubrics (IFEval), every judge except Claude Sonnet 4.5 passed its own failures more often than other models' failures. On subjective medical rubrics (HealthBench) the picture was mixed. Llama, Claude Haiku, Claude Sonnet and Qwen 4B scored below 1 there, which means no self-preference, and gpt-oss-120B scored exactly 1. GPT-5 was the worst on both tests, 1.47× and 1.54× more likely to pass its own failures. Most of these errors turned a real failure into a pass. A five-family majority vote matched or beat single judges and cut the bias, but did not remove it. |
| Capability-dependent bias, Sept 2026 (https://arxiv.org/html/2609.12002v1) | The headline finding: every judge was more lenient toward stronger writers (r ≥ 0.83). That bias was larger than self-preference. GPT-5 passed its own wrong answers far more often than other judges did, 60 to 67 points more. A majority vote of the other judges cut false passes by 15–22 points and raised accuracy by 5–14 points. |
| "Who Judges Matters", Sept 2026 (https://arxiv.org/abs/2609.17857) | Judges from four open families each favoured their own family by 3.4 to 8.4 points. Changing who sits on the panel changed 18.5% of outcomes. A panel is not neutral by default. |
| PoLL, Verga et al. 2024 (https://arxiv.org/html/2404.18796v2) | A panel of three small models from different families (Command R, Claude 3 Haiku, GPT-3.5) beat a single GPT-4 judge. On TriviaQA, κ was 0.906 against 0.841. It cost 7–8× less. GPT-4's own score swung from κ 0.52 to 0.73 with prompt wording alone. |

What this means for us:

- A judge from another family is the right call. Self-preference shows up as false passes, which
  is exactly the error that would hide bad Claude narration.
- Changing family does not remove leniency toward a strong writer. Every judge shows that bias,
  and Opus is a strong writer. Planted failures and a catch-rate gate are the defence.
- A cross-family vote usually helps a little more. Its value must show in our own numbers before we
  pay for it.
- The judge's own model family also matters (Wataoka; "Who Judges Matters"). We can measure it
  directly with a small control set (section 5).

---

## 4. Practical fit

| | OpenAI | Google Gemini | Open models |
|---|---|---|---|
| JSON schema enforced | Yes, strict Structured Outputs | Yes, a JSON Schema in the response format. Some length keywords are dropped. | Depends on the host. vLLM and SGLang serve these models; OpenRouter lists structured outputs for all candidates. |
| Repeatable runs | When reasoning is on, the GPT-6 guide says to drop `temperature` and `top_p`. It does not mention `seed` (https://developers.openai.com/api/docs/guides/latest-model). Whether `seed` works is **unverified**. | `generationConfig.seed` exists: "If not set, the request uses a randomly generated seed" (https://aistudio.google.com/api-reference/generate-content). Exact repeats are not promised. | Fixed settings on our own server repeat best. Exact repeats are **unverified**. |
| Rate limits | Terra, lowest paid tier: 5,000 requests and 1M tokens per minute (Terra page) | Not shown per model on the docs page. Tier 2 needs $100 spent, Tier 3 needs $1,000 (https://aistudio.google.com/docs/rate-limits). | Set by the host or our own hardware |
| Data use | Not used for training by default. Logs are kept up to 30 days for abuse checks. Zero retention needs OpenAI's approval, and the Batch API does not qualify (https://developers.openai.com/api/docs/guides/your-data). | Paid tier: "Google doesn't use your prompts… or responses to improve our products". Logs are kept "for a limited period". Free tier: humans may read the data (https://aistudio.google.com/docs/terms). **Use the paid tier.** | Stays on our machine when run locally. DeepSeek's own API stores data in China. |
| Already in our repo | Yes. The audio code already calls OpenAI over HTTP with `OPENAI_API_KEY` (`src/audio/eval.py`, `src/audio/_http.py`). | No | No |

- **One interface for all of them.** LiteLLM covers OpenAI, Google AI Studio, Vertex, Anthropic,
  Mistral, DeepSeek, OpenRouter, Ollama, vLLM, Together and Fireworks
  (https://docs.litellm.ai/docs/providers). It passes a JSON schema through to each provider. It
  can also check the reply against the schema on our side (`enable_json_schema_validation`), and it
  can report which models support schemas (`supports_response_schema`)
  (https://docs.litellm.ai/docs/completion/json_mode).
- OpenRouter is a second option: one paid key that reaches every model above.
- Under our coding rules, adding LiteLLM means adding it to `uv.lock` through `make sync`. The judge
  must also be one module that the workbench and the app share.
- Python SDK package names (`openai`, `google-genai`) are from memory. I did not check them on an
  official page this session **(unverified)**.

---

## 5. The experiment

### Labelled data

- **The unit is one (tour, criterion) pair.** The rubric draft has 11 judged criteria: CA5, TH4,
  NA6, NA7, JU4, PA3, PA4, PA5, PA6, FD3 and X10 (`Docs/persona-acceptance-rubric.md`). Leave fact
  review (X10) for a separate study. Fact-checking is a different task.
- **Per criterion, label at least 30 FAILs and 30 PASSes.** That makes about 600 labelled items for
  10 criteria. With 30 FAILs, a measured catch-rate of 80% is only known to within about ±14
  points (95% Wilson interval 63–91%). Fewer FAILs make the gate meaningless.
- **Plant failures, because Opus will rarely fail on its own.** Hand-edit copies of real tours, for
  example:
  - add an unexplained "faubourg"
  - open a stop with the fact every guidebook leads with
  - add a sentence that names three people
  - add violence described in detail for the children's tour
  - move the densest stop to the end

  Mix in every natural failure we find. Report catch-rate on natural and planted failures
  separately, because planted ones are often easier to spot.
- **Source tours.** About 20–25 real tours spread over the 11 personas, plus their edited copies.
  A reference tour runs 4,000–5,300 words (`fixtures/reference-tours/`).
- **Human ceiling.** A second person labels about 100 of the same items. Their agreement with
  Sairam (κ) is the most any judge can be expected to reach.
- **Family-bias control (optional, about $20).** Have GPT and Gemini each rewrite 10 clean tours.
  Then plant the same failures by hand in each version. A rewrite of an already-planted tour would
  often fix the planted failure. If a judge catches fewer failures in its own family's text, we have
  measured self-preference directly.

### Metrics for each candidate

Each candidate runs 3 times on every item, with fixed settings.

- **Catch-rate:** the share of human FAILs the judge marks FAIL.
- **Pass-rate:** the share of human PASSes the judge marks PASS.
- **Cohen's κ** against Sairam, macro-averaged over criteria and shown per criterion, beside the
  human ceiling.
- **Run-to-run consistency:** the share of items where all 3 runs agree. Reasoning models do not
  accept a temperature setting, so this is the real test of repeatability.
- **UNKNOWN rate.** UNKNOWN is an abstention. On a human FAIL it counts as not caught. On a human
  PASS it counts as not passed. Report how often it happens.
- **Quote check.** Every evidence quote must appear word for word in the narration, after
  normalising spaces and quote marks. A machine checks this.
- **Two scoring rules.** Score each run on its own. Also score the product's rule: a must criterion
  passes only if all 3 runs pass. That rule raises catch-rate and lowers pass-rate.

### Cost per candidate

Assumptions:

- 600 items × 3 runs = 1,800 calls.
- Each call reads about 8,500 tokens: about 7,000 of narration plus the persona, request and
  criterion. That makes 15.3M input tokens.
- Each call writes about 2,000 tokens, counting thinking. That makes 3.6M output tokens.
- Standard prices. Batch prices halve the cost. Caching the shared tour text cuts the input cost
  further.

| Candidate | Price | Cost |
|---|---|---|
| `gpt-6.1-terra` | $1 / $5 | about $33 |
| `gpt-6.1-sol` (stronger alternative) | $2 / $10 | about $67 |
| `gemini-4-hydrogen` | $1.50 / $7.50 | about $50 |
| `gemini-4-argon` at the 1 December price | $4 / $20 | about $133 |
| `Qwen3.8-27B` through OpenRouter | $0.15 / $1.875 | about $9, or $0 plus machine time locally |
| `gpt-6-astra`, for reference only | $10 / $50 | about $333 |

The 2-of-3 panel reuses the stored runs and costs nothing extra.

The thinking setting moves these costs. The table assumes moderate thinking:

- Terra at `medium` reasoning
- Gemini at `medium` thinking
- Qwen with thinking at `medium`

At the deepest settings, thinking alone can run to several thousand tokens a call, which would
roughly double or triple each figure. Hydrogen's deepest setting is `xhigh`. Qwen's default is
`xhigh`, so set it lower on purpose. That estimate is **unverified**; measure it on the first 20
calls.

### Decision rule

Apply these in order.

1. **Hard gates.** All replies parse against the schema, allowing one automatic retry. 100% of quotes
   pass the word-for-word check. The UNKNOWN rate on items that have narration is 5% or less.
2. **Accuracy gate, per criterion, under the all-3-runs rule.** Catch-rate of 80% or more and
   pass-rate of 90% or more. These are the thresholds the rubric draft already proposes. Catch-rate
   is checked first, because leniency is the error the research shows most. A criterion that no
   candidate clears is a problem with the criterion, not with the judge. Reword it, and keep it
   advisory until it clears.
3. **Consistency gate.** All 3 runs agree on 90% or more of items.
4. **Rank the survivors** by macro κ, read against the human ceiling. When two candidates are
   within 0.05 κ of each other, pick the cheaper one.
5. **Panel or single judge.** Adopt the 2-of-3 cross-family vote only if it beats the best single
   judge by 0.05 κ or more, or if it lifts a criterion over the accuracy gate. Otherwise use the
   single judge. It is simpler and cheaper.

### Shortlist

| # | Candidate | Why |
|---|---|---|
| 1 | `gpt-6.1-terra` (OpenAI) | OpenAI recommends it for writing. It is cheap, and we already hold an OpenAI key. Run it at medium reasoning. If it fails the gates, rerun with `gpt-6.1-sol`. |
| 2 | `gemini-4-hydrogen` (Google) | Its price is stable and it has the deepest thinking setting. Google's judges had the best published agreement in 2026. |
| 3 | `gemini-4-argon` (Google) | The stronger Google option. It tests whether the extra cost buys accuracy. If Gemini 4 disappoints, fall back to `gemini-3.1-pro-preview`, the best-measured judge in 2026. |
| 4 | `Qwen/Qwen3.8-27B` (open, Alibaba) | A third family, under Apache 2.0, and it runs on our own machine. No published judge results exist for this exact version **(unverified as a judge)**. Its 2026 predecessors did reasonably on preference tasks (JudgeArena). The small Qwen 3 8B did poorly on correctness (κ 0.29), so watch the correctness-style criteria. If it fails, `DeepSeek-V4.1-Flash` through a non-DeepSeek host is the next open choice. |
| — | Panel: Terra + Hydrogen + Qwen, 2 of 3 | Three different families, scored from the same runs. |
