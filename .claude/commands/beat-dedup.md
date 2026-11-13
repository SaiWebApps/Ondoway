---
description: Human-reviewed semantic dedup pass over a city's beats.json. MinHash LSH surfaces candidate pairs; Haiku classifies each; user approves per-pair via interactive CLI; approved actions apply atomically via beats_io.commit.
---

You are a duplicate-beat reviewer for the Ondoway content pipeline. You run a
**two-phase** semantic dedup pass over `data/{city}/beats.json`.

## When to run

- After a re-extraction of a chunk that probably overlaps existing beats (e.g., new book pass over a POI already covered).
- As a post-extraction cleanup when new beats may overlap older ones.

Scope is one city per run. Beats from different cities are never compared.

## Prereqs

1. `git status` clean (or at least `data/{city}/beats.json` and `data/{city}/book-log.json` are clean — you will atomically mutate `beats.json`).
2. `ANTHROPIC_API_KEY` is set in the environment. It is **only** read from env. Never log it, never echo it into reports or audit logs.

## Workflow

### Phase 1 — Report (read-only)

```bash
.venv/bin/python scripts/beat_dedup.py {city} [--threshold 0.5] [--shingle-size 5] [--num-perm 128] [--poi "Val-de-Grace"] --report-only
```

What this does:
- Loads `data/{city}/beats.json`.
- Builds MinHash LSH over 5-gram word shingles (defaults: Jaccard ≥ 0.5, 128 permutations — all CLI-overridable).
- Queries the LSH for candidate pairs; computes exact pairwise Jaccard.
- For each candidate, calls Haiku (`claude-haiku-4-5-20251001`) with a structured tool-call that enforces `classification ∈ {same_story_same_wording, same_story_added_detail, same_story_enhanced_content, different_story}`.
  - On parse fail: one retry with a stricter prompt. Still-failing pairs get labeled `different_story` with `_parse_failed: true` and surface **at the top of the report** for human spot-check.
- Writes a markdown report to `data/{city}/_dedup_review/{iso_ts}.md`.

Surface the report to the user. They read each pair and decide per-pair action.

Threshold tuning: start with defaults. If the MinHash under-retrieves known paraphrased overlaps, lower the threshold (e.g., `--threshold 0.15 --shingle-size 3`) and re-run. This is expected — the default 0.5 is a starting point tuned empirically on the Val-de-Grace run.

### Phase 2 — Apply (interactive, TTY-only)

Applying is **interactive-CLI only**. The markdown report from Phase 1 is a read-only record, never an input channel. The orchestrator refuses to apply if stdin is not a TTY. Agents that drive this skill must hand Phase 2 back to the human.

```bash
.venv/bin/python scripts/beat_dedup.py {city} [same flags as Phase 1]
```

(Omit `--report-only`.) The orchestrator re-runs Phase 1 then drops into an interactive loop. For each pair it prints summary + Haiku classification + recommended action, then prompts:

```
[a]ccept / [s]kip / [i]nsert / [c]ombine / [k]eep-both / [q]uit:
```

- `a` — apply the recommended action.
- `s` → SKIP: remove the newer beat (by `_meta.generated_at`); keep the older.
- `i` → INSERT: no-op; both beats stay. Use when adding detail.
- `c` → COMBINE: prompt for merged text (blank line ends input); remove both beats, insert one new beat with `merged_from: [id_a, id_b]` and a new normalized hash.
- `k` → KEEP_BOTH: set `dedup_reviewed: true` on both beats. Use when they're actually different stories.
- `q` — quit. Already-decided pairs apply; remaining pairs un-applied. (Resumable — rerun the skill.)

Apply flow:
1. All decisions mutate an in-memory copy of `beats.json`.
2. The orchestrator calls `scripts.beats_io.commit(final_beats, final_log, ...)` — staged write, validator check, atomic rename. On validator fail the apply step rolls back; `beats.json` and `book-log.json` are left byte-identical.
3. One JSON-line per applied decision is appended to `data/{city}/_dedup_review/_log.jsonl` with fields `{ts, pair, jaccard, classification, action, approver}`.

## Action semantics (canonical)

| Action | `beats.json` mutation |
|---|---|
| `SKIP` | Remove the beat with the newer `_meta.generated_at`. |
| `INSERT` | No mutation. |
| `COMBINE` | Remove both; insert one new beat with user-supplied merged text, fresh hash, and `merged_from: [id_a, id_b]`. |
| `KEEP_BOTH` | Set `dedup_reviewed: true` on both beats. |

## COMBINE source-integrity rules (pipeline guardrails #1, #4)

- **Every sentence in the merged text must be traceable to one of the two beats' source passages.** Do not introduce facts from outside the two beats, and do not resolve factual conflicts by LLM world-knowledge.
- **When the two beats disagree on a verifiable fact** (name, date, number, attribution), COMBINE is not appropriate — fall back to `KEEP_BOTH` and flag the conflict. A human decides which source is canonical before any merge.
- Agents proposing merged text must cite, for each claim, which source beat it came from — so the approving human can verify before pressing `a`.

## Verification

After apply, confirm that no unreviewed near-duplicates remain on the scope's target POI (e.g., Val-de-Grace):

```bash
.venv/bin/python scripts/verify_vdg_ac8.py
```

Exit 0 means: no two VdG beats score Jaccard ≥ 0.8 unless both carry `dedup_reviewed: true` and were the subject of a `KEEP_BOTH` entry in `_dedup_review/_log.jsonl`.

## Guardrails

- Never mutate `beats.json` outside `beats_io.commit`. If the in-memory mutation is buggy the validator catches it and rolls back.
- Report files (`_dedup_review/*.md`) are gitignored; only `_log.jsonl` is tracked (audit trail).
- Never print `ANTHROPIC_API_KEY` or any environment values to stdout, reports, or the audit log.
- If the LSH surfaces pairs outside the intended POI (e.g., cross-POI matches during a VdG-scoped run), STOP and ask the user before applying.
- `.venv/bin/pytest tests/ -x` must be green after any apply.
