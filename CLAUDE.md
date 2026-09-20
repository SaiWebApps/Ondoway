# Ondoway — coding rules for the whole project

These are coding rules, not a process. They bind every file under `src/`, `mobile/lib/`,
`frontend/`, `scripts/` and `tests/`, and every session or agent that edits them.

## Product invariants

1. **Never build it twice.** Before writing any function, module, file or code path, find
   the existing one that answers the question and EXTEND it. The workbench and the app run
   the EXACT SAME code for everything they share — routing, planning, authoring, audio: one
   algorithm, one construction site, imported by both surfaces. A parallel copy "for the
   workbench" or "for the app" is a defect even when it is byte-identical today, because the
   two drift the moment either is edited.

2. **Modularity and single responsibility.** One module, one concern. One function, one
   job, with a name that says which. No god-functions, no flags that switch a function
   between unrelated concerns, no logic duplicated across layers. Well-engineered,
   well-architected code — not vibecode.

3. **No mocks, fakes or stubs in product code.** Nothing under `src/` or `mobile/lib/` may
   contain a fake provider, a stub client or a test-only branch. Test doubles live under
   `tests/` (and `mobile/test/`) only, and the product guards that every selectable
   provider really leaves the machine stay green.

4. **Dependencies come from `make sync`.** Python packages are installed by `make sync`
   (Apple mirror: `make sync-apple`), which resolves `uv.lock`. Never `pip install` into the
   environment by hand, and never edit `requirements.txt` — regenerate it with
   `make requirements`.

## How to work

5. **Full source file context.** Before modifying any source file, read the ENTIRE file with
   the Read tool. Never guess an import, a signature, a decorator or the surrounding class
   scope from memory or from an excerpt. Logs, stdout and data files are different — inspect
   those however you like, with `tail`, `head`, `grep` or a pipe.

6. **Direct test execution.** Run the targeted test in the foreground and read the result:
   `uv run pytest tests/test_x.py -k <test_name> --tb=short`. Do not redirect test output to
   a background `.log` file and poll it. Use `make test-file FILE=tests/test_x.py` when the
   test needs the database, Valhalla or credentials.

7. **Fast iteration.** One loop, four steps: write or update the targeted test → make the
   change → confirm that test passes → `make lint`. No mutation proofs, no sabotage-and-restore
   cycles, no ladder of ceremonial re-verification between steps. Run the full suite
   (`make test`, or `make audit` for lint plus suite) once when the milestone is done.

8. **The owner reads plain words.** Anything written for the owner — a report, a
   question, a status update — uses everyday words and short sentences: no project
   jargon, no codenames, no internal shorthand, one idea per sentence. If a term
   would need explaining, explain it or don't use it. Reports use the shared team
   skill's concise outcome, evidence, obligation and decision shape; no editor agent
   is required.

9. **Comments and rules are written in the present tense.** A comment, rule file, or agent
   definition states the constraint that holds now — never the incident that motivated it.
   No dates, no owner quotes, no session stories, no "this exists because X once did Y".
   Incidents belong in git history and `.claude/LEARNINGS.md`; a constraint that cannot be
   justified without its origin story points at the LEARNINGS entry instead of retelling it.
   The same applies when cleaning up: removing a thing means removing every reference to it
   — registrations, ignore rules, docs, agent prompts — in the same change, so nothing keeps
   describing a world that no longer exists. `/cleanup` (`.claude/commands/cleanup.md`) is
   the procedure that makes this mechanical; every removal runs it.
