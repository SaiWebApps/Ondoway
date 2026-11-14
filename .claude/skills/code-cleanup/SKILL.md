---
name: code-cleanup
description: Find cleanup work across one area of the codebase and apply the safe part of it. Seed scans map the area, then read-only finder agents each hunt one concern — history told as comments (dates, commit hashes, plan codenames, document citations), verification theatre (hashes, seals and fingerprints that guard against nothing a user or caller does), comment noise, dead code, duplicate copies, legacy paths, defensive cruft and weak types. Writer agents that each own a set of files apply the safe findings; the owner decides the rest. Use when asked to map, audit, sweep or clean up the codebase, or to find leftover AI residue. To remove one named thing completely, use /cleanup instead.
argument-hint: "<area: src/tour | src/api | mobile/lib | frontend | scripts | a tests/ slice> [lens ids]"
---

# /code-cleanup: scan, find by concern, write by file

```
snapshot ─▶ seed scans ─▶ finder agents, one per lens (read-only)
         ─▶ triage: merge by place, sort apply / ask / skip
         ─▶ writer agents, one per file set (the only editors)
         ─▶ tests + lint ─▶ owner decides the ask list
```

Discovery and removal are separate jobs. This skill finds. When a finding is
"this whole thing is dead", it can also go to `/cleanup <thing>`, which
removes every reference and proves the absence.

## Step 0 — scope, cost and a snapshot

1. **One area per run.** Measure it: `git ls-files <area> | xargs cat | wc -c`.
   The finders work from scans and targeted reads, so an area of about 1.5 MB
   is fine. Take `tests/` separately, in slices that match the source area.
2. **Cost.** A run is about 8 finders and 5 writers. On an Opus-class model,
   tell the owner the area and the lens list and wait for a yes. A smoke run
   takes two lenses: `history` and `theatre`.
3. **Snapshot.** The tree may hold the owner's uncommitted work. Record it
   without touching it, so this run's changes can be shown on their own:
   ```bash
   git update-ref refs/code-cleanup/pre-<area-slug> "$(git stash create)"
   git diff --stat refs/code-cleanup/pre-<area-slug>    # must be empty now
   ```
   Re-run that diff before launching writers. If it is not empty, the tree
   changed under you: stop and tell the owner.
4. **Run folder.** `printenv TMPDIR` as its own Bash call, then
   `mkdir -p <that path>/code-cleanup/<area-slug>-<YYYY-MM-DD>`. Reports and
   work orders live there, out of the working tree.

## Step 1 — seed scans

Save the output to `<RUN DIR>/seeds.txt`. Use `git grep -P`: the `-E`
engine on macOS ignores `\b`.

```bash
git grep -nP '(#|//|/\*|\*).*\b20\d{2}-[01]\d-[0-3]\d\b' -- <area>
git grep -nP '(#|//|/\*|\*).*\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b' -- <area>
git grep -nP '(#|//|/\*|\*).*(\bW\d{2,4}\b|\bADR\b|§|[Ss]ection \d|Docs/|\.md\b|LEARNINGS)' -- <area>
git grep -nP '(#|//|/\*|\*).*\b(previously|used to|no longer|was replaced|now uses|owner said|the owner|this session|incident)\b' -- <area>
uv run ruff check --select F401,F841,ERA001 <area>        # Python areas only
git ls-files | grep -E '/w[0-9]{2,4}_'                    # files named after codenames
```

Save per-file counts of hash words to `<RUN DIR>/hash-counts.txt`:
`git grep -nicE '(sha256|sha1|md5|fingerprint|seal|receipt|checksum|frozen)' -- <area> | sort -t: -k2 -rn`.

## Step 2 — finder agents, one per lens

Launch one `general-purpose` agent per lens, in the background. Every prompt
is byte-identical up to the lens, so they share a cached prefix: launch the
first alone, then the rest in the next message.

```
You are one of eight read-only cleanup finders working on <AREA> in <REPO>. Do not launch agents.
Find cleanup work in <AREA> within your concern only, and propose each change with the evidence that makes it safe. Do not report bugs or style outside your concern.
Context you need:
- The project's rules: <the comment, citation and one-copy rules from CLAUDE.md, in one paragraph>.
- The area is large. Do not read everything. Start from <RUN DIR>/seeds.txt and <RUN DIR>/hash-counts.txt, then use git grep, the Read tool with offset/limit, and `codegraph callers|impact|node <symbol>`.
- Ignore fixtures, data and generated code.
You are read-only: do not edit, create or delete any file except your report.
Every path:line and quoted output in your report must come from a tool call you made in this run; drop a finding you cannot cite.
Write ONLY JSON lines to <RUN DIR>/<LENS ID>.jsonl with the Write tool, one per finding:
{"file": "...", "line": <n>, "sites": ["path:line", ...every site, not a sample], "title": "<short>", "evidence": "<the grep, codegraph or tool output that proves it>", "change": "<concrete enough to apply without re-investigating; for a comment, the exact replacement text or 'delete'>", "confidence": "high|medium|low", "risk": "<what could break: dynamic wiring, a public API, a persisted value, the app/workbench contract>", "conflicts": "<other lenses this change touches, or empty>"}
At most 40 findings, strongest first. End by replying with just the report path and the number of findings.
Your lens id: <ID>
Your concern: <LENS BRIEF>
```

### The lenses

1. **`history`** — history told as a comment. A comment or docstring states a
   constraint that holds now. Flag every one that says when, where, why or by
   whom something came to be: ISO dates, commit hashes, plan codenames,
   decision-record numbers, section numbers, document paths, owner quotes,
   session stories, "DELETED <date>" tombstones, "previously / used to / no
   longer / was replaced by". Propose the present-tense rewrite that keeps
   the constraint, or deletion. A file named after a codename is a finding.
2. **`theatre`** — hashes, seals, fingerprints, receipts, checksums, frozen
   snapshots and validators. Follow each value to its final reader. Keep it
   only when that reader does something a user, the app or an outside
   service needs: a cache key, an idempotency or dedup key, a download
   integrity check, a provider request id. Report it when the final reader is
   another check: its own guard, its own tests, or a script that verifies
   stored files against hashes of themselves. A seal checked by a seal is
   still theatre, and "changing it would change stored hashes" is not a
   reason to keep it.
3. **`dead-code`** — symbols and modules nothing in production calls.
   Separate "no reference anywhere" from "only tests reference it".
4. **`legacy`** — compatibility shims, deprecated aliases, fallbacks for old
   shapes, branches kept only so old test doubles still work.
5. **`dedup`** — two copies of one calculation. Name the survivor, every
   caller that moves, and any behavioural difference between the copies.
6. **`slop`** — comments that restate the code, narrate, lecture, shout in
   ALL CAPS, or argue; ASCII banners; commented-out code.
7. **`defensive`** — guards around trusted internal calls, silent fallbacks
   that hide failures, `except Exception` around local code.
8. **`weak-types`** — `Any`, bare `dict`, `object`, `type: ignore`. When no
   type checker runs in the repo, these go to the checklist, not the edits.

## Step 3 — triage (this session)

1. Count findings from the files (`grep -c . <RUN DIR>/*.jsonl`), never from
   memory.
2. Sort each finding:
   - **apply** — comment rewrites (`history`, `slop`); dead code with no
     reference anywhere; guards whose risk is "none". These change no
     behaviour.
   - **ask** — every `theatre` item; anything that reaches into tests,
     changes behaviour, deletes a name other code could import, changes a
     persisted field or an env var, or merges copies across files.
   - **skip** — weak types without a type checker; low-confidence items.
3. Group the apply items into work orders by file, one writer per file set.
   No two writers share a file. Give the largest file its own writer. When
   `history` and `slop` hit the same comment, the writer writes one rewrite
   that satisfies both; a deletion beats a rewrite of the same lines. A
   comment item whose sites span file sets goes to every owning writer, each
   told to edit only its own sites.
4. Write the ask list to `.claude/runs/<YYYY-MM-DD>-code-cleanup-<area-slug>/ask-list.md`
   and put it to the owner as at most four grouped questions, while the
   writers run.

## Step 4 — writer agents

Each writer gets its work order, its file list and the list of files it must
not touch. Each writer:

- reads every file it edits in full before editing;
- re-checks every item against the code and drops any that no longer hold,
  re-running a whole-repo `git grep` before deleting a symbol;
- writes comments as plain present-tense sentences, with no dates, history,
  shouting or document citations;
- edits no test unless the owner approved a change that needs it;
- runs `uv run ruff check` and an import of each edited module, and fixes
  only what it broke;
- writes `<RUN DIR>/orders/<writer>.result.jsonl` with one line per item:
  applied or dropped, and why.

Writers do not run the suite in parallel: every checkout shares one test
database. Approved changes that touch tests run as their own writers after the
safe pass, grouped so that no two share a file, with `make test-file`
one file at a time.

## Step 5 — prove it and report

1. `git diff --shortstat refs/code-cleanup/pre-<area-slug> -- <area>` for the
   counts.
2. `make _track-pure` (the database-free suite) and `make _track-tour-quality`
   when the area is the tour engine; `make lint`. A lint error on a line this
   run did not change belongs to the owner's work: name it, do not fix it.
3. Report in plain words: what changed, the counts, the test results, that
   nothing is committed, the ask list's path, and that
   `git update-ref -d refs/code-cleanup/pre-<area-slug>` removes the snapshot.

Delete `<RUN DIR>` when done. Commit nothing.
