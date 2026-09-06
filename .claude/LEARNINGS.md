# Ondoway Project Learnings

Mistakes made and rules to prevent them. Every entry is from a real incident.

---

## 1. Never skip a failing test

**Incident:** `test_workbench_ui.py` failed to collect because `playwright` wasn't installed. Instead of installing the dependency, Claude tried to `--ignore` the file and run the rest of the suite.

**Rule:** If a test fails to collect or run due to a missing dependency, install the dependency. If it's missing from `pyproject.toml`, add it there first. `--ignore` and `--exclude` are never acceptable responses to a missing dependency.

---

## 2. Verify the environment before doing any work

**Incident:** The entire session ran against a broken venv (stale shebangs from the `geotada` to `ondoway` directory rename). This wasn't discovered until 4+ hours in when `pip install` was needed.

**Rule:** At the start of every session, run these checks:
```bash
python -c "import pytest; print('OK')"   # venv works
head -1 .venv/bin/pip                      # shebangs point to current directory
git remote -v                              # remote URL is correct
docker ps                                  # containers running
cat .env | head -3                         # correct database target
```
Five commands. Ten seconds. Non-negotiable.

---

## 3. Never destroy a working environment without a recovery plan

**Incident:** `python3 -m venv .venv --clear` wiped the existing venv (which had all packages installed despite broken shebangs). Then `pip install` failed due to the corporate proxy. Result: empty venv, no way to reinstall packages.

**Rule:** Before running `rm -rf .venv` or `--clear`, verify that `pip install` actually works in the current environment (test with a trivial package). If the network/proxy is broken, fix the specific problem (e.g., shebangs) surgically instead of nuking everything.

---

## 4. The test bar is the FULL suite

**Incident:** `make test-unit` (159 tests) was used as the verification bar throughout the session, while `pytest tests/` (the full suite) would have revealed the playwright collection error immediately.

**Rule:** The bar is `pytest tests/ -v` with zero exclusions. Every test file must collect. Every test must either pass or skip with a documented, automatic reason (e.g., `@pytest.mark.skipif` for missing Neo4j). Manual exclusions (`--ignore`, `--exclude`) are never acceptable.

---

## 5. Check git remote before pushing

**Incident:** `git push origin main` was blocked by a hook that prevents direct pushes to default branches. Claude created a feature branch and tried to make a PR. The user had to point out this was unnecessary for a personal repo.

**Rule:** Check `git remote -v` at the start of the session. Understand the repo's push model (personal repo = push to main, shared repo = feature branches). Don't let generic safety hooks override the user's explicit instructions.

---

## 6. Run linting after agents generate code

**Incident:** Six agents generated code (tests, security fixes, quality improvements). None of them ran `make lint`. The 104 lint errors were only discovered hours later when `/build-fix` was invoked.

**Rule:** After any agent writes or modifies Python code, run `make lint` immediately. Add it to the agent's verification step. Don't batch lint checks -- they compound.

---

## 7. Declared dependencies must match test imports

**Incident:** `tests/test_workbench_ui.py` imports `playwright`, but `playwright` was not in `requirements.txt`. This meant `pytest tests/` could never succeed from a clean install.

**Rule:** Every `import` in the test suite must be satisfiable from the declared dependencies in `pyproject.toml`. When writing new tests that use a package, verify it's declared. When reviewing the dependency list, cross-reference against `grep -r "^import\|^from" tests/`.

---

## 8. Gitignored files don't get swept by rename agents

**Incident:** Four agents renamed all Travlr/Geotada references to Ondoway across the codebase, but `.env.local` and `.env.cloud` (gitignored) were skipped.

**RESOLVED (2026-04-30):** All remaining travlr/geotada references swept, including gitignored files, Makefile, and workbench-map-ux spec files.

**Rule:** When doing a project-wide rename, explicitly include gitignored config files (`.env*`, `.env.local`, `.env.cloud`) in the sweep. `grep -rn` doesn't search gitignored files by default.

---

## 9. Corporate proxy blocks pip but not uv

**Incident:** Multiple attempts to `pip install` failed with `ProxyError: 403 Forbidden`. The corporate proxy blocks direct access to `pypi.org` from pip.

**Rule:** This project uses `uv` for dependency management. Never use `pip install` directly. Use `uv sync`, `uv add`, or `uv run`. The `~/.config/uv/uv.toml` is configured to work with the corporate proxy. If you encounter a pip error, the answer is always "use uv instead."

---

## 10. Diagnose before retrying

**Incident:** When pip failed, Claude tried `--trusted-host`, `--no-build-isolation`, and other flags without first diagnosing the root cause (corporate proxy blocking pypi.org). Three failed retries before stopping to think.

**Rule:** One failed attempt = stop and diagnose. Copy the exact error. Identify the root cause with evidence. Only then choose the fix. "Try a different flag" is not diagnosis.

---

## 11. Always use `make` — never raw `uv run`

**Incident:** Claude repeatedly ran `uv run pytest`, `uv run python -c "import pytest; pytest.main(...)"` and other raw commands instead of using the Makefile targets (`make test-local`, `make test-unit`, `make lint`). This bypassed the Makefile's env setup (e.g., `make test-local` copies `.env.local` to `.env.test` first), led to inconsistent results, and wasted time debugging environment issues that the Makefile handles automatically.

**Rule:** Every command has a `make` target. Use it. If a command doesn't have a `make` target, add one first and then use it. Never bypass the Makefile with raw `uv run` commands. The Makefile encodes the correct sequence of operations.

---

## 12. Start ALL sidecars before running any tests

**Incident:** Only started `make db-test-up` (port 7688) but not `make db-up` (port 7687). Then `make test-local` failed because it targets port 7687. Wasted 30 minutes debugging "sandbox blocks network" when the real issue was the container wasn't running.

**Rule:** At session start, run `make db-up` AND `make db-test-up`. Both containers must be healthy before any test run. Don't start one and assume the other isn't needed.

---

## 13. Every `.env*` file must set `NEO4J_DATABASE`

**Incident:** `.env.local` and `.env.test` lacked `NEO4J_DATABASE=neo4j`. The fallback was the cloud database name `8a37bbaf` from the shell environment. Tests connected to the local Neo4j but tried to use a nonexistent database, producing `Graph not found: 8a37bbaf`.

**Rule:** Every `.env*` file (`.env.local`, `.env.test`, `.env.cloud`) must explicitly set `NEO4J_DATABASE`. For local/test Neo4j, it's `neo4j`. Never rely on fallback from other env files or shell environment.

---

## 14. Don't blame infrastructure — read the error

**Incident:** When integration tests skipped with "Neo4j not available", Claude blamed the Apple sandbox for blocking network. The real causes were: (a) wrong port because container wasn't started, (b) missing `NEO4J_DATABASE` env var, (c) `connection.py` overriding test env vars. All discoverable by reading the error message and checking env vars.

**Rule:** "The sandbox did it" is not a diagnosis. Read the exact error. Check env vars. Check which containers are running. Check which port is being targeted. The answer is almost always a configuration issue, not an infrastructure block.

---

## 15. Verify Cypher relationship directions against the schema

**Incident:** `find_matching_beats()` in `crud/trips.py` used `(POI)<-[:AT_POI]-(NarrativeBeat)` to traverse from POI to Beat. But `AT_POI` goes from ItineraryItem to POI, not from Beat to POI. The correct path is `(POI)-[:HAS_BEAT]->(NarrativeBeat)`. This caused all trip generation integration tests to fail with empty results.

**Rule:** Before writing a Cypher query that traverses a relationship, verify the direction and endpoint types against `src/schema/definitions.py`. The relationship types and their semantics: `HAS_BEAT` = POI→Beat, `AT_POI` = ItineraryItem→POI, `TAGGED_WITH` = Beat→Lens. Don't guess.

---

## 16. When blocked by environment constraints, STOP and tell the user immediately

**Incident:** CocoaPods couldn't install from Claude Code (proxy blocks rubygems.org). Instead of immediately telling the user "run this one command in your terminal," Claude silently pivoted to a web fallback, then fought Ruby version conflicts, then wasted 30 minutes before admitting the constraint.

**Rule:** When any command fails due to an environment constraint Claude cannot fix (sudo required, proxy blocking downloads, missing system tools), immediately: (1) state the exact error in one sentence, (2) state the exact command the user needs to run, (3) STOP and wait. Do not attempt workarounds. Do not pivot to alternatives. "I'll work around it" is how 30 minutes get wasted on a dead end.

---

## 17. Verify the ENTIRE toolchain before writing any code

**Incident:** Claude wrote 7 React Native screens and an Expo project without first verifying: (a) CocoaPods was installed, (b) `pod install` could reach cdn.cocoapods.org, (c) Xcode SDK version matched the simulator runtime, (d) `xcodebuild` could target the available simulators. All four were broken. Zero screens were ever visible on the simulator.

**Rule:** Before writing any code for a new platform/toolchain, verify every step of the build chain end-to-end with a trivial "hello world" first. For React Native iOS: `pod --version` → `xcodebuild -showsdks` → `xcrun simctl list runtimes` → verify SDK and runtime versions match → build a blank app → see it on the simulator. Only then start writing real screens. Writing code you can't build is writing fiction.

---

## 18. Never pivot silently — the user said mobile, not web

**Incident:** User explicitly said "create a mobile app." When iOS native build failed, Claude silently switched to Expo web (`--web` flag) without telling the user. The web version was also broken. The user discovered an empty page and asked "why are you creating a web app?"

**Rule:** When the user requests X and X is blocked, do not silently deliver Y. State: "X is blocked because [reason]. I need [thing] to unblock it. Should I proceed differently?" The user decides, not Claude.

---

## 19. Never label errors "pre-existing" to justify ignoring them

**Incident:** During the /dev workflow for the Paris image fix, `make lint` showed 126 errors. Claude labeled them "pre-existing" (they existed before the current changes) and proceeded to spawn agents and continue the workflow without fixing any of them. The user had to stop the entire workflow and demand enforcement. Claude's rationalization was "the Developer only touched Dart files, so Python lint isn't relevant" — but the CLAUDE.md rules and /dev workflow rules both explicitly say ALL errors must be fixed. Claude read those rules and violated them anyway out of laziness.

**Rule:** `make lint` must produce 0 errors before ANY commit, ANY agent spawn, or ANY declaration of done. "Pre-existing" is a diagnosis label for a report, never an exemption from fixing. **Nothing enforces this mechanically** — a `~/.claude/hooks/lint-enforcer.sh` was claimed here for months and `~/.claude/hooks/` does not exist at all (corrected 2026-07-25). Running `make lint` yourself IS the enforcement. Run it at the start of every workflow. If it has errors, fix them FIRST — before any other work. No rationalizing, no "these are in files I didn't touch," no "these are style not bugs." 0 errors or blocked.

---

## 20. Flutter asset changes require `flutter clean` — tests are not proof

**Incident:** The Paris image wasn't rendering on the iOS simulator. A previous session had already investigated and left a memory entry saying "try `make flutter-clean && make flutter-ios`." Claude ignored this prior work, spawned a Planner agent that theorized a progressive JPEG / Impeller incompatibility, spawned a Developer agent to convert the image, then declared success when tests passed. But tests use a separate asset bundle — they proved nothing about the iOS simulator build. The user ran `make flutter-ios`, saw no image, and said "You have failed." The actual fix was `make flutter-clean` — a stale build cache. The image conversion was unnecessary. 30+ minutes wasted on a 2-minute fix.

**Rule:** (1) When a memory entry exists about the exact issue being fixed, follow its instructions FIRST — before planning, before agents, before theorizing. Prior sessions are prior work. (2) After ANY change to files under `mobile/assets/`, run `make flutter-clean` before `make flutter-test` or `make flutter-ios`. Flutter's incremental build does NOT reliably detect binary asset changes. (3) Tests passing does NOT mean UI works. Visual changes require simulator verification via `make flutter-ios` + screenshot. (4) Never run `make flutter-test` in background — Flutter buffers stdout, producing 0 output until finish.

---

## 21. Never run Flutter tests in background

**Incident:** `make flutter-test` was run with `run_in_background`. Flutter buffers stdout completely, so the output file stayed at 0 bytes for over 3 minutes. Claude polled it with `sleep 5` loops — over 20 polling attempts, all showing empty output — wasting the user's time. The CLAUDE.md Rule 8 and prevent-laziness hook now mechanically block this.

**Rule:** Always run `make flutter-test` and `make test` in the foreground. Never use `run_in_background` for Flutter commands. If a foreground command is taking long, tell the user what you're waiting for — don't go silent.

---

## 22. A milestone's test command is the narrowest thing that proves it

**Incident:** Two tracker rows in one run could not be flipped for reasons that had nothing to do with whether the work was done. One carried `make test` as its verification command: the tracker re-runs that command to verify a completion claim, so the box sat unflippable for hours while a cloud database — infrastructure that milestone never touched — refused the full bar. The other named a test that had only ever been *planned*: the name went into the row at planning time and was never written into the repo, so no run could ever satisfy it, and the row stayed `pending` long after the feature that satisfied it had shipped under different test names. In both cases the board lied about work that was finished and committed.

**Rule:** An `issue-add --test-command` names the NARROWEST command that proves that milestone and that already runs — one test file, or one pytest node id. Never `make test` / `make audit`: the full bar answers "is the repo green", which is a different question from "is this milestone done", and it drags unrelated infrastructure into a completion claim. If the milestone's test lands under a different name than the plan guessed, re-point the row with `issue-set --id M{n} --test-command "…"` before claiming the step.

---

## 23. A model may never sit on the panel that judges its own output

**Incident:** A re-author pipeline rewrote 524 copied beats with `claude-opus-5`, then
verified them with a two-model panel of `claude-opus-5` and `claude-sonnet-5`, requiring
both to find nothing before auto-approving. 384 beats were approved and reported to the
owner as "verified by two independent models". The author was half the panel, so every
approval rested on a model declining to flag its own work. The module's own docstring
stated "one model checking its own work is not independence" eight lines above the
constant that broke it. Three adversarial subagents found it independently; a one-line
grep confirmed it.

**Rule:** The set of judging models must exclude the model that produced the artifact —
assert it in a test, because a constant defined in one file and a constant defined in
another will drift. Two model names are not independence on their own: judges reading the
same prompt with the same evidence share every blind spot the prompt writes in. Real
independence needs information asymmetry between roles, such as extracting claims from
source and from output in calls that never see each other's input. And a verdict that
ships unread must store what the judges actually answered; a hard-coded `models` field and
an empty `reason` are a claim that verification happened, not evidence of it.

---

## 24. Never tune a gate on its own escalation rate

**Incident:** The same pipeline's verification prompt was hand-calibrated on 15 beats
until escalation fell from 47% to 20%, and the 20% was reported as evidence the gate was
now well-aimed. Escalation rate moves monotonically with leniency, so the tuning could not
distinguish "fewer false alarms" from "blind to more defects", and with no held-out set it
measured fit and nothing else. The design could not measure its own miss rate at all,
because no beat with a known defect was ever put through it — which made "384 are clean"
unfalsifiable.

**Rule:** Calibrate a gate against a labelled set with known defects and score it on
FALSE-NEGATIVE rate. Defect injection gives that ground truth almost free: take good
outputs and plant a fabricated number, a deleted claim, a lifted phrase.
`scripts/coverage_calibrate.py` is the bake-off method to copy. A rate you tuned toward is
never evidence about the thing you tuned it on.

---

## 25. A metric the producer optimises against is not evidence about the producer

**Incident:** Bodies copied from guidebooks were rewritten with an instruction to keep the
same content and share none of the phrasing, then cleared by `verbatim_ratio` — the 8-word
shingle overlap the instruction was effectively written against. Every rewrite scored
under the threshold and this was reported as evidence the copying was retired. It was not:
91% of rewrites with a multi-sentence source still followed the source's sentence order
exactly, reproducing its selection and arrangement, which is the part copyright protects.
Separately, 81 cleared rewrites contained an 8+ word verbatim run, because the ratio
divides matched shingles by body length and a lifted clause inside a long body scores near
zero.

**Rule:** When a generator is told to satisfy a metric, that metric stops being evidence
about the generator's output and becomes a description of its instructions. Evidence has
to come from a check the generator was not aimed at — here, sentence-order alignment
against the source, and a longest-run measure rather than a length-normalised ratio. State
what a metric literally measures before quoting it as proof of anything broader.
