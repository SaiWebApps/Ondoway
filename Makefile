export ONDOWAY_ALLOW_INSECURE_AUTH_SECRETS := 1
SHELL := /bin/bash

# ════════════════════════════════════════════════════════════════════════════
#  PREREQUISITES
# ════════════════════════════════════════════════════════════════════════════
# Every target below opens by NAMING the capabilities it needs.  scripts/preflight.py
# resolves that list in dependency order, probes each one for real (a live Cypher
# query, a health endpoint, an installed executable), starts or installs the ones it
# is allowed to, and exits non-zero naming the exact command for the ones it cannot.
# Nothing in a recipe runs until its whole list is satisfied.
#
# This replaced hand-rolled `_ensure-*` helpers whose success message was simply the
# last statement in a chain that ignored failures: with the Docker daemon down, the
# old `db-up` printed "neo4j ready at bolt://localhost:7687" and exited 0, and the
# real failure surfaced two steps later disguised as a data problem.
#
#   make preflight            check everything and repair what is repairable
#   make preflight-list       describe every requirement and how it is restored
#   PREFLIGHT_AUTOFIX=0 ...   report only; never start or install anything
#
# preflight runs on the SYSTEM python3 and imports only the standard library, so it
# can still report that uv or Docker is missing on a machine with nothing set up.
PREFLIGHT := python3 scripts/preflight.py

# A LANE is one concurrent track of work: the main checkout (LANE empty) or a
# worktree (LANE=2, LANE=3).  A lane owns EVERY graph it writes — dev, pytest and
# workbench — so two agents cannot wipe each other's data: both suites full-wipe
# their own graph, so sharing one produces phantom failures rather than a slow
# queue.  Routing is deliberately NOT per-lane: it is stateless read-only lookup
# with no write to collide over, and a copy would mean a second 1.6 GB of tiles.
#
# LANE= is the only knob.  It selects the whole set and moves the preflight
# requirements with it (PRE_PYTEST below), so a lane cannot silently run against a
# graph nobody started:   make test-file LANE=2 FILE=tests/test_x.py::TestY::test_z
#
# Adding lane 4 is one compose service per graph plus one preflight row each — do
# that rather than queueing behind a busy lane.  `make test` and `make audit` are
# deliberately NOT parameterised: the definitive bar always runs the canonical set.
# The defaults stay LITERAL -- `test`, not `test$(LANE)` -- because
# scripts/preflight.py resolves `db-$(TEST_PROFILE)` by reading these lines, and
# it deliberately takes only literal values so it can never start a substitution
# chain (tests/test_preflight.py fails the moment a name stops resolving). The
# lane override therefore happens below, where it costs the parser nothing.
# A worktree pins its lane (and any other machine-local make setting) here; the
# file is gitignored, so the committed defaults below stay canonical.
-include .make.local
LANE ?=
TEST_PROFILE ?= test
DEV_DB ?= dev
WORKBENCH_PROFILE ?= workbench
ifneq ($(LANE),)
TEST_PROFILE := test$(LANE)
DEV_DB := dev$(LANE)
WORKBENCH_PROFILE := workbench$(LANE)
endif
export ONDOWAY_LANE := $(LANE)

ENV_EXEC := uv run python scripts/dev_env.py exec
LOCAL_EXEC := $(ENV_EXEC) --profile local --
TEST_EXEC := $(ENV_EXEC) --profile $(TEST_PROFILE) --
WORKBENCH_EXEC := $(ENV_EXEC) --profile $(WORKBENCH_PROFILE) --
RENDER_LOCAL_EXEC := $(ENV_EXEC) --profile local --render --
RENDER_TEST_EXEC := $(ENV_EXEC) --profile test --render --
CLOUD_EXEC := $(ENV_EXEC) --profile cloud --render --
NO_PROXY_LIST := api.resend.com,resend.com,www.googleapis.com,googleapis.com,api.anthropic.com,anthropic.com,api.github.com,github.com
LIVE_TEST_FILES := \
	tests/test_audio_functional.py \
	tests/test_audio_says_story.py \
	tests/test_magic_link_live.py
GOLDEN_TEST_FILES := \
	tests/test_narration_coherence.py \
	tests/test_tour_golden_ile.py \
	tests/test_tour_golden_pdv.py
GRADE_TEST_FILES := tests/test_tour_audit.py tests/test_tour_grade.py
INVARIANT_TEST_FILES := tests/test_tour_invariants_live.py
# The three dev-graph pytest files. Each opens its own localhost:7687 driver and
# its own module `client`, and never touches the 7688-family test graphs — their
# needs_db tag comes only from fixture NAMES matching conftest's auto-tagger set —
# so they run as their own concurrent track while the DB workers keep 7688/90/91.
DEVGRAPH_TEST_FILES := tests/test_trip_api.py tests/test_persona_traces.py \
	tests/test_tour_authoring_gates.py
DEVGRAPH_IGNORES := $(foreach f,$(DEVGRAPH_TEST_FILES),--ignore=$(f))
# Set by the `test` orchestrator, which scrubs __pycache__ ONCE before launching
# its concurrent tracks: a per-recipe scrub while a sibling track's pytest is
# importing would race the interpreter's own .pyc writes.
SKIP_PYC_SCRUB ?=

# Reusable prerequisite sets.  A target names one of these, or spells its own list.
PRE_PY := uv python-deps
PRE_LOCAL_GRAPH := uv python-deps db-dev dev-data
PRE_TOUR := uv python-deps db-dev dev-data valhalla
PRE_PYTEST := uv python-deps db-$(TEST_PROFILE) db-$(DEV_DB) dev-data valhalla
PRE_FLUTTER := flutter flutter-deps
# The full union `make test` will need, checked once up front so a missing Render
# credential fails in seconds rather than twenty minutes into the suite.
PRE_FULL_SUITE := uv python-deps db-test db-dev db-workbench dev-data valhalla \
	playwright-browser flutter flutter-deps render-key

LINT_PATHS := src/ tests/ scripts/dev_env.py scripts/ensure_dev_data.py \
	scripts/preflight.py scripts/db_parity.py scripts/upload_paris.py scripts/check_audio_setup.py \
	scripts/tour_batch_candidate.py scripts/score_saved_tours.py \
	scripts/score_gold_text.py scripts/human_reference_tours.py \
	scripts/ingest_calibrate.py scripts/claim_conflicts.py scripts/ingest_job.py \
	scripts/ingest_batch.py \
	scripts/tour_golden_diff.py scripts/poi_visit_duration.py \
	scripts/poi_opening_hours.py scripts/poi_place_category.py \
	scripts/poi_body_places.py scripts/poi_place_judgements.py \
	scripts/poi_queues.py scripts/poi_trigger_radius.py scripts/sync_poi_exports.py \
	scripts/report_visit_durations.py scripts/tour_build.py scripts/dedup_review.py \
	scripts/tour_batch_review.py scripts/lint_process_files.py \
	scripts/corpus_report.py scripts/verbatim.py \
	scripts/extract_validators.py scripts/audit_extraction.py scripts/wipe_beats.py scripts/rebuild_book_log.py

# Reports a missing credential or a wrong endpoint as a sentence. This was a bare
# `assert` inside `python -c`, so the answer to "is my config right?" was a stack trace.
CHECK_PROFILE := import os,sys; \
name,port=sys.argv[1],sys.argv[2]; \
uri=os.environ.get("NEO4J_URI",""); \
sys.exit(f"{name}: NEO4J_URI is {uri!r}, expected bolt://localhost:{port}") if uri!=f"bolt://localhost:{port}" else None; \
sys.exit(f"{name}: RESEND_API_KEY is absent -- the Render fetch returned an incomplete environment") if not os.environ.get("RESEND_API_KEY") else None; \
print(f"{name}: localhost:{port} + fresh Render credentials")

DB ?= dev
TARGET ?= local

# Every local graph, plus how its compose service and volume are derived from the
# name. Spelled once so a new lane is one compose service and one preflight row,
# never an edit to four copies of the same list down in the DATABASE targets.
LOCAL_DBS := dev test workbench dev2 test2 workbench2 dev3 test3 workbench3
db_service = $(if $(filter dev,$(1)),neo4j,neo4j-$(1))
db_volume = ondoway_$(subst -,_,$(call db_service,$(1)))_data
check_db = @echo " $(LOCAL_DBS) " | grep -q " $(DB) " || \
	{ echo "ERROR: DB must be one of: $(LOCAL_DBS); never cloud." >&2; exit 2; }

.PHONY: \
	help doctor setup bootstrap preflight preflight-list \
	render-auth-setup render-auth-status config-status \
	sync sync-apple requirements lint format flutter-analyze \
	test audit test-file test-live test-workbench golden-probe golden-diff \
	score-saved-tours score-gold-text score-human-tours \
	tour-batch-plan tour-batch-live tour-batch-review-plan tour-batch-review-live \
	db-up db-down db-status db-reset db-parity \
	valhalla-up valhalla-down valhalla-status valhalla-build-tiles \
	api workbench dashboard \
	flutter-web flutter-ios flutter-device flutter-pub-get flutter-clean \
	survey-area-candidates fix-area-radii backfill-provenance backfill-poi-role \
	backfill-name-key wiki-fetch gen-within-edges validate-beats deploy prune-orphans \
	fetch-boundary geocode-pois poi-visit-duration poi-opening-hours \
	poi-place-category poi-visit-report poi-body-places poi-place-judgements \
	poi-queues poi-trigger-radius sync-poi-exports tour-build \
	measure-planned-audio measure-governor \
	onboard-city flutter-ipa testflight render-status setup-audio \
	aura-resume-proof flutter-test clean \
	_track-pure _track-db _track-db-live _track-devgraph _track-surfaces \
	_track-tour-quality _test-golden _test-grade _test-invariants _test-cloud

# ════════════════════════════════════════════════════════════════════════════
#  QUICKSTART
# ════════════════════════════════════════════════════════════════════════════
##@ QUICKSTART

help: ## Show the supported public targets.
	@awk 'BEGIN {FS = ":.*?## "} \
		/^##@ / { printf "\n\033[1m%s\033[0m\n", substr($$0, 5); next } \
		/^[a-zA-Z][a-zA-Z0-9_-]+:.*?## / { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' \
		$(MAKEFILE_LIST)
	@echo ""

# A diagnostic, so it exits 0 even when things are missing: a fresh clone is
# MEANT to be missing things, and the first command a new developer runs must not
# look like a broken tool. `make preflight` is the one that fixes them.
doctor: ## Report every prerequisite without starting or installing anything.
	@$(PREFLIGHT) --all --report --label "this machine"

preflight: ## Check every prerequisite and repair everything that is repairable.
	@$(PREFLIGHT) --all --label "the whole project"

preflight-list: ## Describe every requirement a target can declare, and how it is restored.
	@$(PREFLIGHT) --list

setup: ## Set up everything from a fresh clone (same as bootstrap).
	@$(MAKE) --no-print-directory bootstrap

bootstrap: ## Provision a complete local development environment from a fresh clone.
	@$(PREFLIGHT) --label bootstrap uv python-deps docker-daemon \
		db-dev db-test db-workbench dev-data valhalla-tiles \
		flutter flutter-deps playwright-browser render-key
	@echo ""
	@echo "✓ Local development environment is ready."
	@echo "  Routing is NOT started here. The map data is downloaded, but building"
	@echo "  tiles from it takes well over an hour, so the first target that needs"
	@echo "  routing (test, api, workbench, tour-build) starts it and waits."
	@echo "  To get it building now, in the background:  make valhalla-up"
	@echo ""
	@echo "  One toolchain is NOT installed here, because it is large and only"
	@echo "  some workflows need it:"
	@echo "    iOS builds (flutter-ios, flutter-ipa, testflight):  make preflight-list"

# ════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ════════════════════════════════════════════════════════════════════════════
##@ CONFIGURATION

render-auth-setup: ## Store or replace the Render API key in macOS Keychain.
	@$(PREFLIGHT) --label render-auth-setup $(PRE_PY)
	@uv run python scripts/dev_env.py auth-setup

render-auth-status: ## Validate the Keychain Render API key against ondoway-api.
	@$(PREFLIGHT) --label render-auth-status $(PRE_PY) render-key
	@uv run python scripts/dev_env.py auth-status

config-status: ## Fetch Render fresh and validate the final local/test/workbench boundaries.
	@$(PREFLIGHT) --label config-status $(PRE_PY) render-key
	@$(RENDER_LOCAL_EXEC) python -c '$(CHECK_PROFILE)' local 7687
	@$(RENDER_TEST_EXEC) python -c '$(CHECK_PROFILE)' test 7688
	@$(ENV_EXEC) --profile workbench --render -- python -c '$(CHECK_PROFILE)' workbench 7689

sync: ## Install Python dependencies from public PyPI.
	@$(PREFLIGHT) --label sync uv
	uv sync --extra test --extra dev --extra aws

sync-apple: ## Install dependencies from Apple's mirror while preserving the public lockfile.
	@$(PREFLIGHT) --label sync-apple uv
	@curl -sI --max-time 5 https://pypi.apple.com/simple/ >/dev/null 2>&1 || \
		{ echo "ERROR: pypi.apple.com unreachable; use make sync." >&2; exit 1; }
	@backup=$$(mktemp -t ondoway-uv-lock) && cp uv.lock "$$backup" && \
	{ UV_DEFAULT_INDEX=https://pypi.apple.com/simple uv sync --extra test --extra dev --extra aws; ec=$$?; \
		cp "$$backup" uv.lock || { echo "ERROR: could not restore the public uv.lock from $$backup" >&2; ec=1; }; \
		rm -f "$$backup"; exit $$ec; }

requirements: ## Regenerate requirements.txt from uv.lock.
	@$(PREFLIGHT) --label requirements $(PRE_PY)
	#: requirements.txt IS the deployed image's dependency list
	# (Dockerfile pip-installs it), and the local voice is a production fallback
	# tier, not a developer convenience. Omitting it here builds an image whose
	# TTS_FALLBACK names a provider the container cannot import.
	uv export --no-dev --no-editable --no-emit-project -o requirements.txt
	@if grep -q "pypi.apple.com" requirements.txt; then \
		rm -f requirements.txt; \
		echo "ERROR: Apple index URLs leaked into requirements.txt. Re-run where" >&2; \
		echo "       public PyPI is reachable so the committed lock stays public." >&2; \
		exit 1; \
	fi

# ════════════════════════════════════════════════════════════════════════════
#  CODE QUALITY
# ════════════════════════════════════════════════════════════════════════════
##@ CODE QUALITY

lint: ## Run the Python linter and the process-file lint.
	@$(PREFLIGHT) --label lint $(PRE_PY)
	uv run ruff check $(LINT_PATHS)
	uv run python scripts/lint_process_files.py

dedup-review: ## Find one question this codebase answers in two places.
	@$(PREFLIGHT) --label dedup-review $(PRE_PY) render-key
	@$(RENDER_LOCAL_EXEC) uv run python scripts/dedup_review.py

format: ## Format Python and apply safe lint fixes.
	@$(PREFLIGHT) --label format $(PRE_PY)
	uv run ruff format $(LINT_PATHS)
	uv run ruff check --fix-only $(LINT_PATHS)

flutter-analyze: ## Run Dart static analysis.
	@$(PREFLIGHT) --label flutter-analyze $(PRE_FLUTTER)
	cd mobile && NO_PROXY=pub.dev,*.pub.dev no_proxy=pub.dev,*.pub.dev flutter analyze

# ════════════════════════════════════════════════════════════════════════════
#  TEST
# ════════════════════════════════════════════════════════════════════════════
##@ TEST

# `LANE=` on every delegation, so this stays the definitive bar even when invoked
# from a lane: PRE_FULL_SUITE above names the canonical graphs, and without the
# override a `make test LANE=2` would preflight those and then run the shards
# against lane 2's. One bar, one set of graphs, one meaning of green.
#
# FIVE CONCURRENT TRACKS, grouped by the resources they own so no two tracks
# share mutable state: db+live (the 7688-family graphs, then the live shard that
# needs them free), pure (CPU only), devgraph (the 7687 corpus + Valhalla),
# surfaces (Dart VM, workbench's own 7689, Aura reads), and tour-quality (7687
# reads + Valhalla, serialized within itself). db+live is the longest track and
# streams to the terminal; the others buffer to per-track logs printed below it.
# Every track always runs to completion — a red track never kills a sibling
# mid-wipe — and any red track fails the bar with exit 2.
test: ## THE definitive suite: Python, Flutter, browser, tour, live-provider, and cloud parity — five concurrent tracks.
	@$(PREFLIGHT) --label test $(PRE_FULL_SUITE)
	@find tests src -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@set -u; logs=$$(mktemp -d /tmp/ondoway-test-tracks.XXXXXX); \
	echo "── five tracks run concurrently; db+live (the longest) streams here, the rest print below ──"; \
	$(MAKE) --no-print-directory LANE= SKIP_PYC_SCRUB=1 _track-pure         > "$$logs/pure.log" 2>&1 & pure_pid=$$!; \
	$(MAKE) --no-print-directory LANE= SKIP_PYC_SCRUB=1 _track-devgraph     > "$$logs/devgraph.log" 2>&1 & devgraph_pid=$$!; \
	$(MAKE) --no-print-directory LANE= SKIP_PYC_SCRUB=1 _track-surfaces     > "$$logs/surfaces.log" 2>&1 & surfaces_pid=$$!; \
	$(MAKE) --no-print-directory LANE= SKIP_PYC_SCRUB=1 _track-tour-quality > "$$logs/tour-quality.log" 2>&1 & quality_pid=$$!; \
	$(MAKE) --no-print-directory LANE= SKIP_PYC_SCRUB=1 _track-db-live; db_rc=$$?; \
	wait $$pure_pid; pure_rc=$$?; \
	wait $$devgraph_pid; devgraph_rc=$$?; \
	wait $$surfaces_pid; surfaces_rc=$$?; \
	wait $$quality_pid; quality_rc=$$?; \
	for t in pure devgraph surfaces tour-quality; do \
		echo ""; echo "────── track $$t ──────"; cat "$$logs/$$t.log"; \
	done; \
	echo ""; fail=0; \
	for pair in "db+live:$$db_rc" "pure:$$pure_rc" "devgraph:$$devgraph_rc" "surfaces:$$surfaces_rc" "tour-quality:$$quality_rc"; do \
		name=$${pair%%:*}; rc=$${pair##*:}; \
		if [ "$$rc" -eq 0 ]; then echo "✓ track $$name: PASS"; \
		else echo "✗ track $$name: FAIL (exit $$rc)"; fail=1; fi; \
	done; \
	rm -rf "$$logs"; \
	[ "$$fail" -eq 0 ] || exit 2; \
	echo "✓ Every definitive test shard passed."

audit: ## Run lint and then the definitive test suite.
	@$(PREFLIGHT) --label audit $(PRE_FULL_SUITE)
	@$(MAKE) --no-print-directory LANE= lint
	@$(MAKE) --no-print-directory LANE= test

test-file: ## Run one test file safely. Usage: make test-file FILE=tests/test_x.py [LIVE=1].
	@test -n "$(FILE)" || { echo "ERROR: FILE is required." >&2; exit 2; }
	@if [ "$(LIVE)" = "1" ]; then \
		$(MAKE) --no-print-directory test-live FILE="$(FILE)"; \
	else \
		$(PREFLIGHT) --label "test-file" $(PRE_PYTEST) || exit $$?; \
		find tests src -name __pycache__ -exec rm -rf {} + 2>/dev/null || true; \
		$(TEST_EXEC) uv run pytest "$(FILE)" -o addopts= -v; \
	fi

test-live: ## Run every live-provider test with a fresh full Render environment.
	@$(PREFLIGHT) --label test-live $(PRE_PYTEST) render-key
	@$(MAKE) --no-print-directory render-auth-status
	@if [ -z "$(SKIP_PYC_SCRUB)" ]; then find tests src -name __pycache__ -exec rm -rf {} + 2>/dev/null || true; fi
	@$(RENDER_TEST_EXEC) env \
		ONDOWAY_LIVE_TESTS=1 \
		NO_PROXY="$(NO_PROXY_LIST)" no_proxy="$(NO_PROXY_LIST)" \
		uv run pytest $(or $(FILE),$(LIVE_TEST_FILES)) -o addopts= -m live -v --durations=15 $(PYTEST_ARGS)

# Declares `valhalla` because this shard really does route: test_workbench_ui.py
# contains an unstubbed tour-generation section, and relying on a sibling shard to
# have started routing first is what made it pass while producing nothing.
# It also declares port-8001, the port its managed server binds.
test-workbench: ## Run the Playwright workbench suite against isolated Neo4j 7689.
	@$(PREFLIGHT) --label test-workbench uv python-deps db-$(TEST_PROFILE) db-$(WORKBENCH_PROFILE) valhalla playwright-browser
	@if [ -z "$(SKIP_PYC_SCRUB)" ]; then find tests src -name __pycache__ -exec rm -rf {} + 2>/dev/null || true; fi
	@$(TEST_EXEC) env NO_PROXY="$(NO_PROXY_LIST)" no_proxy="$(NO_PROXY_LIST)" \
		uv run pytest tests/test_workbench_ui.py -o addopts= -v --tb=short --durations=15

# The filter matches the marker ANYWHERE in the line, not at its start. `-q -s` makes
# pytest write its progress dots to stdout with no newline, so the test's own print
# lands on the same line as them (`.GOLDEN-OVERLAP Ile 6/17 …`). `lstrip()` strips
# whitespace, never dots, so a start-anchored match found nothing and this target
# exited 2 on every run — green goldens included. Measured 2026-08-24 at W8.6.
golden-probe: ## Print golden overlap counts using provisioned dev data and Valhalla.
	@$(PREFLIGHT) --label golden-probe $(PRE_PYTEST)
	@set -o pipefail; $(TEST_EXEC) uv run pytest $(GOLDEN_TEST_FILES) \
		-o addopts= -m golden -q -s 2>&1 | tee /dev/stderr | \
		python3 -c 'import sys; \
lines=[l[l.index("GOLDEN-OVERLAP"):] for l in sys.stdin if "GOLDEN-OVERLAP" in l]; \
sys.stdout.writelines(lines); \
sys.exit(0 if lines else "no GOLDEN-OVERLAP lines were printed (see the run above)")'

score-saved-tours: ## Score every saved tour artifact against the current rubric. $0, no DB, no containers.
	@$(PREFLIGHT) --label score-saved-tours $(PRE_PY)
	@$(LOCAL_EXEC) uv run python scripts/score_saved_tours.py $(SCORE_ARGS)

score-gold-text: ## Score the owner's own gold text against the rubric — calibration. $0, no DB.
	@$(PREFLIGHT) --label score-gold-text $(PRE_PY)
	@$(LOCAL_EXEC) uv run python scripts/score_gold_text.py --compare

ingest-calibrate: ## Judge phases over fixtures/ingestion/defects.json on the MOCK client; caught/missed per class vs the baseline. $0: judged classes are scripted, gates classes measured.
	@$(PREFLIGHT) --label ingest-calibrate $(PRE_PY)
	@$(LOCAL_EXEC) uv run python scripts/ingest_calibrate.py --client mock $(ARGS)

ingest-calibrate-live: ## The same over the LIVE judge (spends). Prints the cost estimate and stops unless ARGS=--yes; ARGS="--yes --update-baseline" records the accepted rates.
	@$(PREFLIGHT) --label ingest-calibrate-live $(PRE_PY) render-key
	@$(RENDER_LOCAL_EXEC) uv run python scripts/ingest_calibrate.py --client live $(ARGS)

ingest-job: ## ONE headless ingest job on the LIVE client into the gitignored data-ingest/ root (spends). Usage: make ingest-job CITY=new_york CHUNK_DIR=Books/new_york/lonely-planet-new-york-city ARGS="--chunk chunk-07-upper-east-side --as-of 2023 [--yes]". Prints the estimate and stops unless --yes.
	@test -n "$(CITY)" || { echo "ERROR: CITY is required." >&2; exit 2; }
	@test -n "$(CHUNK_DIR)" || { echo "ERROR: CHUNK_DIR is required (a Books/<city>/<book> dir with a manifest.json)." >&2; exit 2; }
	@$(PREFLIGHT) --label ingest-job $(PRE_PY) render-key
	@$(RENDER_LOCAL_EXEC) env INGEST_PROVIDER=anthropic INGEST_DATA_ROOT=data-ingest \
		uv run python scripts/ingest_job.py --city $(CITY) --chunk-dir $(CHUNK_DIR) $(ARGS)

ingest-batch: ## Recover a finished Batch API round (-e): per-request stop reason, tokens, priced cost, parsed claim count; saved to data-ingest/batches/. Usage: make ingest-batch BATCH=msgbatch_...
	@test -n "$(BATCH)" || { echo "ERROR: BATCH is required (a msgbatch_... id)." >&2; exit 2; }
	@$(PREFLIGHT) --label ingest-batch $(PRE_PY) render-key
	@$(RENDER_LOCAL_EXEC) uv run python scripts/ingest_batch.py --batch $(BATCH) $(ARGS)

claim-conflicts: ## City-wide report of the same fact stated with different values across beats (P6 sees one place at a time). Usage: make claim-conflicts CITY=new_york [ARGS=--apply]. $0.
	@test -n "$(CITY)" || { echo "ERROR: CITY is required." >&2; exit 2; }
	@$(PREFLIGHT) --label claim-conflicts $(PRE_PY)
	@$(LOCAL_EXEC) uv run python scripts/claim_conflicts.py --city $(CITY) $(ARGS)

score-human-tours: ## Score the two human-authored reference tours against the rubric. $0, no DB.
	@$(PREFLIGHT) --label score-human-tours $(PRE_PY)
	@$(LOCAL_EXEC) uv run python -m scripts.human_reference_tours

golden-diff: ## Diff one golden fixture. Usage: make golden-diff FIXTURE=pdv_round_trip_60min.
	@test -n "$(FIXTURE)" || { echo "ERROR: FIXTURE is required." >&2; exit 2; }
	@$(PREFLIGHT) --label golden-diff $(PRE_TOUR)
	@$(LOCAL_EXEC) uv run python scripts/tour_golden_diff.py "$(FIXTURE)"

# THE FIVE TRACKS. Each names the resources it owns; two tracks never share
# mutable state, which is what makes running them concurrently sound. The
# python invocations carry -p no:cacheprovider because concurrent pytest
# sessions would race on one .pytest_cache. Timeouts are a HANG-BREAKER, not a
# perf police: --timeout-method=signal fails the one slow test; `thread` killed
# the whole xdist worker and failed every test after it.

# Pure tests: no DB at all. 8 workers, capped below `auto` on purpose — the
# planner tracks (devgraph, tour-quality) and the workbench's Chromium need
# cores of their own while this track runs beside them.
_track-pure:
	@$(PREFLIGHT) --label _track-pure $(PRE_PYTEST)
	@echo "── pure tests (no DB; 8 workers) ──"
	@$(TEST_EXEC) env NO_PROXY="$(NO_PROXY_LIST)" no_proxy="$(NO_PROXY_LIST)" \
		uv run pytest tests/ -v -m "not needs_db" $(DEVGRAPH_IGNORES) -n 8 --dist loadfile --timeout=300 --timeout-method=signal --durations=15 -p no:cacheprovider $(PYTEST_ARGS)

# DB tests: the three workers map to 7688/7690/7691 (tests/conftest.py
# _XDIST_WORKER_DB), so this track preflights ALL THREE test graphs — with only
# db-test declared, workers 1 and 2 ran against stopped containers and two
# thirds of the shard errored at fixture setup. The dev-graph files are ignored
# here: they never touch these graphs and run as their own track.
_track-db:
	@$(PREFLIGHT) --label _track-db $(PRE_PYTEST) db-test2 db-test3
	@echo "── DB tests (3 workers, one test graph each) ──"
	@$(TEST_EXEC) env NO_PROXY="$(NO_PROXY_LIST)" no_proxy="$(NO_PROXY_LIST)" \
		uv run pytest tests/ -v -m "needs_db" $(DEVGRAPH_IGNORES) -n 3 --dist loadfile --timeout=300 --timeout-method=signal --durations=15 -p no:cacheprovider $(PYTEST_ARGS)

# test-live needs the 7688-family graphs free (its files use the conftest DB
# fixtures), so it chains after the DB shard inside one track.
_track-db-live:
	@$(MAKE) --no-print-directory LANE= SKIP_PYC_SCRUB=1 _track-db
	@$(MAKE) --no-print-directory LANE= SKIP_PYC_SCRUB=1 test-live

# The dev-graph tours: whole files (their untagged tests included), two workers
# — one carries test_trip_api, the other test_persona_traces plus the gates.
_track-devgraph:
	@$(PREFLIGHT) --label _track-devgraph $(PRE_PYTEST)
	@echo "── dev-graph tours (trip_api, persona traces, authoring gates; 2 workers on 7687) ──"
	@$(TEST_EXEC) env NO_PROXY="$(NO_PROXY_LIST)" no_proxy="$(NO_PROXY_LIST)" \
		uv run pytest $(DEVGRAPH_TEST_FILES) -v -n 2 --dist loadfile --timeout=300 --timeout-method=signal --durations=15 -p no:cacheprovider $(PYTEST_ARGS)

# The surfaces: Dart VM, the workbench's own 7689 graph + managed server, and
# read-only Aura parity. Nothing here touches a graph another track writes.
_track-surfaces:
	@$(MAKE) --no-print-directory LANE= flutter-test
	@$(MAKE) --no-print-directory LANE= SKIP_PYC_SCRUB=1 test-workbench
	@$(MAKE) --no-print-directory LANE= _test-cloud

# Tour quality: golden, grade and invariants all read the 7687 corpus and route
# through Valhalla, serialized WITHIN the track so their Valhalla load stays at
# today's level; the goldens refuse an unrouted walk by name either way.
_track-tour-quality:
	@$(MAKE) --no-print-directory LANE= _test-golden
	@$(MAKE) --no-print-directory LANE= _test-grade
	@$(MAKE) --no-print-directory LANE= _test-invariants

_test-golden:
	@$(PREFLIGHT) --label _test-golden $(PRE_PYTEST)
	@$(TEST_EXEC) uv run pytest $(GOLDEN_TEST_FILES) -o addopts= -m golden -v --durations=15 $(PYTEST_ARGS)

_test-grade:
	@$(PREFLIGHT) --label _test-grade $(PRE_PYTEST)
	@$(TEST_EXEC) uv run pytest $(GRADE_TEST_FILES) -o addopts= -m grade -v --durations=15 $(PYTEST_ARGS)

_test-invariants:
	@$(PREFLIGHT) --label _test-invariants $(PRE_PYTEST)
	@$(TEST_EXEC) uv run pytest $(INVARIANT_TEST_FILES) -o addopts= -m invariants -v --durations=15 $(PYTEST_ARGS)

_test-cloud:
	@$(PREFLIGHT) --label _test-cloud $(PRE_PY) render-key
	@$(MAKE) --no-print-directory render-auth-status
	@echo "→ Aura parity: read-only session; pytest and reset targets never receive Aura."
	@$(CLOUD_EXEC) uv run python -m scripts.aura_ensure_running
	@$(CLOUD_EXEC) env ONDOWAY_CLOUD_READ_ONLY=1 uv run python -m scripts.db_parity

# ════════════════════════════════════════════════════════════════════════════
#  PAID TOUR OPERATIONS
# ════════════════════════════════════════════════════════════════════════════
##@ PAID TOUR OPERATIONS

tour-batch-plan: ## Build the sealed Premium plan without constructing a paid client.
	@$(PREFLIGHT) --label tour-batch-plan $(PRE_TOUR)
	@$(LOCAL_EXEC) uv run python -m scripts.tour_batch_candidate $(if $(CASE),--case "$(CASE)",)

# Refuses an OUTPUT_ROOT that RESOLVES to the frozen control arm, however it is spelled
# — trailing slash, leading ./, an absolute path, a .. segment. Resolved and compared,
# never string-matched. $(CURDIR) is the base for both sides so the answer does not
# depend on where make was invoked from.
CHECK_BATCH_ROOT := import os,sys; \
given,root=sys.argv[1],sys.argv[2]; \
frozen=os.path.realpath(os.path.join(root,"data/certification/tour-batch-v1")); \
sys.exit(f"ERROR: OUTPUT_ROOT {given!r} resolves to the frozen control arm {frozen}, which the release gate compares against. Write elsewhere.") if os.path.realpath(os.path.join(root,given))==frozen else None

# OUTPUT_ROOT is how you avoid overwriting the frozen control arm, so it is REQUIRED.
# The script defaults --output-root to data/certification/tour-batch-v1/ — the sealed
# batch the release gate compares AGAINST — and until 2026-08-25 this recipe had no way
# to pass anything else, so the documented way to run a regeneration was also the way to
# destroy its own baseline. Passing it OPTIONALLY was the same defect one step quieter:
# omitting it silently authored into the control arm. Both refusals here are convenience;
# scripts/tour_batch_candidate.py refuses the same root itself, which is the guard a
# direct `python -m` call cannot walk around.
#   make tour-batch-live PLAN_SHA256=... OUTPUT_ROOT=data/certification/tour-batch-v2
# TRANSPORT defaults to the Message Batches API — the same sealed requests at half
# price, with the batch_id persisted to disk before polling so a crash resumes by
# id instead of paying twice. TRANSPORT=sync restores the per-stop streaming lane.
tour-batch-live: ## Execute an approved Premium plan. PLAN_SHA256=... and OUTPUT_ROOT=... both required.
	@test -n "$(PLAN_SHA256)" || { echo "ERROR: PLAN_SHA256 is required." >&2; exit 2; }
	@test -n "$(OUTPUT_ROOT)" || { echo "ERROR: OUTPUT_ROOT is required; it must not be the frozen control arm data/certification/tour-batch-v1." >&2; exit 2; }
	@python3 -c '$(CHECK_BATCH_ROOT)' "$(OUTPUT_ROOT)" "$(CURDIR)"
	@$(PREFLIGHT) --label tour-batch-live $(PRE_TOUR) render-key
	@$(RENDER_LOCAL_EXEC) env ONDOWAY_TOUR_BATCH_APPROVED=1 \
		uv run python -m scripts.tour_batch_candidate --live \
		--approve-plan-sha256 "$(PLAN_SHA256)" --max-workers "$(or $(MAX_WORKERS),4)" \
		--output-root "$(OUTPUT_ROOT)" --transport "$(or $(TRANSPORT),batch)"

tour-batch-review-plan: ## Rebuild a sealed semantic-review plan without provider calls.
	@$(PREFLIGHT) --label tour-batch-review-plan $(PRE_TOUR)
	@$(LOCAL_EXEC) uv run python -m scripts.tour_batch_review \
		$(if $(BATCH_ROOT),--batch-root "$(BATCH_ROOT)",) \
		$(if $(REVIEW_ROOT),--review-root "$(REVIEW_ROOT)",) \
		$(if $(ARCHIVAL_ROOT),--archival-root "$(ARCHIVAL_ROOT)",)

tour-batch-review-live: ## Execute an approved semantic-review plan with fresh Render credentials.
	@test -n "$(REVIEW_PLAN_SHA256)" || { echo "ERROR: REVIEW_PLAN_SHA256 is required." >&2; exit 2; }
	@$(PREFLIGHT) --label tour-batch-review-live $(PRE_TOUR) render-key
	@$(RENDER_LOCAL_EXEC) env ONDOWAY_TOUR_BATCH_REVIEW_APPROVED=1 \
		uv run python -m scripts.tour_batch_review --live \
		--approve-review-plan-sha256 "$(REVIEW_PLAN_SHA256)" \
		--max-workers "$(or $(MAX_WORKERS),4)" \
		$(if $(BATCH_ROOT),--batch-root "$(BATCH_ROOT)",) \
		$(if $(REVIEW_ROOT),--review-root "$(REVIEW_ROOT)",) \
		$(if $(CALIBRATION_REUSE_ROOT),--calibration-reuse-root "$(CALIBRATION_REUSE_ROOT)",) \
		$(if $(ARCHIVAL_ROOT),--archival-root "$(ARCHIVAL_ROOT)",)

# ════════════════════════════════════════════════════════════════════════════
#  DATABASE
# ════════════════════════════════════════════════════════════════════════════
##@ DATABASE

# Starting a database IS the preflight requirement `db-<name>`: it starts the
# service if needed and then proves readiness by running a real Cypher query
# inside the container. It cannot report success on a database that is absent.
db-up: ## Start one local Neo4j service. Usage: make db-up DB=<one of $(LOCAL_DBS)>.
	$(check_db)
	@$(PREFLIGHT) --label "db-up DB=$(DB)" db-$(DB)

db-down: ## Stop one local Neo4j service without deleting data. Usage: make db-down DB=...
	$(check_db)
	@$(PREFLIGHT) --label "db-down DB=$(DB)" docker-daemon
	docker compose stop "$(call db_service,$(DB))"

db-status: ## Show all local Neo4j service states.
	@$(PREFLIGHT) --label db-status docker-daemon
	@docker compose ps $(foreach d,$(LOCAL_DBS),$(call db_service,$(d)))

db-reset: ## Delete exactly one local Neo4j volume. Usage: make db-reset DB=<one of $(LOCAL_DBS)>.
	$(check_db)
	@$(PREFLIGHT) --label "db-reset DB=$(DB)" docker-daemon
	@set -e; \
	docker compose stop "$(call db_service,$(DB))"; \
	docker compose rm -f "$(call db_service,$(DB))"; \
	docker volume rm -f "$(call db_volume,$(DB))"; \
	echo "✓ Deleted only local volume $(call db_volume,$(DB))."

db-parity: ## Compare committed data with local dev or read-only Aura. Usage: make db-parity TARGET=local|cloud.
	@case "$(TARGET)" in \
		local) $(PREFLIGHT) --label "db-parity TARGET=local" uv python-deps db-dev && \
			$(LOCAL_EXEC) uv run python -m scripts.db_parity $(CITY) ;; \
		cloud) $(PREFLIGHT) --label "db-parity TARGET=cloud" $(PRE_PY) render-key && \
			$(CLOUD_EXEC) env ONDOWAY_CLOUD_READ_ONLY=1 \
			uv run python -m scripts.db_parity $(CITY) ;; \
		*) echo "ERROR: TARGET must be local or cloud." >&2; exit 2 ;; \
	esac

# ════════════════════════════════════════════════════════════════════════════
#  ROUTING
# ════════════════════════════════════════════════════════════════════════════
##@ ROUTING

VALHALLA_ROOT = $(shell dirname "$$(git rev-parse --git-common-dir)")

# `valhalla` declares `valhalla-tiles`, so a clone with no map data is told to run
# valhalla-build-tiles immediately, instead of polling a doomed endpoint for ten
# minutes and then failing.
valhalla-up: ## Start Valhalla and wait for a healthy routing endpoint.
	@$(PREFLIGHT) --label valhalla-up valhalla

valhalla-down: ## Stop Valhalla without deleting tiles.
	@$(PREFLIGHT) --label valhalla-down docker-daemon
	docker compose -f "$(VALHALLA_ROOT)/docker-compose.yml" \
		--project-directory "$(VALHALLA_ROOT)" stop valhalla

valhalla-status: ## Check the Valhalla health endpoint.
	@curl -fs --noproxy '*' --max-time 3 http://localhost:8002/status >/dev/null \
		&& echo "✓ Valhalla healthy." \
		|| { echo "Valhalla is not answering on :8002. Start it: make valhalla-up" >&2; exit 1; }

# Download to a temporary name and move into place only on success. Writing
# straight to the final path meant that interrupting this at 50 MB of 465 MB left
# a truncated extract, which the `valhalla-tiles` probe then reported as
# satisfied — a green report on broken data, in the one target exempted from the
# preflight mechanism. The tile build would consume it and produce a quietly
# wrong road network. preflight's own repair already does it this way.
valhalla-build-tiles: ## Download Paris and New York OSM extracts for tile building.
	@mkdir -p "$(VALHALLA_ROOT)/valhalla/custom_files"
	@set -e; dir="$(VALHALLA_ROOT)/valhalla/custom_files"; \
	download() { \
		echo "→ downloading $$1"; \
		curl -L --fail -o "$$dir/$$1.partial" "$$2" || { rm -f "$$dir/$$1.partial"; exit 1; }; \
		mv "$$dir/$$1.partial" "$$dir/$$1"; \
	}; \
	download ile-de-france-latest.osm.pbf \
		https://download.geofabrik.de/europe/france/ile-de-france-latest.osm.pbf; \
	download new-york.osm.pbf \
		https://download.bbbike.org/osm/bbbike/NewYork/NewYork.osm.pbf; \
	echo "✓ OSM extracts downloaded. The first routing start builds tiles from them."

# ════════════════════════════════════════════════════════════════════════════
#  RUN
# ════════════════════════════════════════════════════════════════════════════
##@ RUN

api: ## Start the local API with dev data and fresh Render provider credentials.
	@$(PREFLIGHT) --label api $(PRE_TOUR) render-key port-8000
	@$(RENDER_LOCAL_EXEC) env NO_PROXY="$(NO_PROXY_LIST)" no_proxy="$(NO_PROXY_LIST)" \
		uv run uvicorn src.api.app:app --host 127.0.0.1 --port 8000 --reload

workbench: ## Start the local editorial workbench with dev data and fresh Render credentials.
	@$(PREFLIGHT) --label workbench $(PRE_TOUR) render-key port-8000-reusable
	@$(RENDER_LOCAL_EXEC) bash scripts/workbench.sh

dashboard: ## Start the local dashboard with the validated dev profile.
	@$(PREFLIGHT) --label dashboard $(PRE_LOCAL_GRAPH) port-8080
	@$(LOCAL_EXEC) uv run python -m src.server

flutter-web: ## Run the Flutter web app on port 3000.
	@$(PREFLIGHT) --label flutter-web $(PRE_FLUTTER) port-3000
	cd mobile && flutter run -d chrome --web-port=3000

# Pick a simulator that exists on THIS machine: a booted one if there is one,
# else the newest available iPhone. Override with `make flutter-ios SIM=<udid>`.
SIM_PICK = xcrun simctl list devices -j 2>/dev/null | python3 -c 'import json,sys;\
d=json.load(sys.stdin).get("devices",{});\
c=[v for r in sorted(d) for v in d[r] if v.get("isAvailable")];\
b=[v["udid"] for v in c if v.get("state")=="Booted"];\
i=[v["udid"] for v in c if "iPhone" in v.get("name","")];\
print(next(iter(b or i or [""])))' 2>/dev/null
SIM_TARGET = $(if $(filter command line,$(origin SIM)),$(SIM),$(shell $(SIM_PICK)))

flutter-ios: ## Run the Flutter app in an iOS simulator with a local API.
	@$(PREFLIGHT) --label flutter-ios $(PRE_TOUR) render-key port-8000 $(PRE_FLUTTER) xcode cocoapods
	@test -n "$(SIM_TARGET)" || { \
		echo "ERROR: no iOS simulator found on this machine." >&2; \
		echo "       Open Xcode > Settings > Components and install a simulator runtime," >&2; \
		echo "       or name one yourself:  make flutter-ios SIM=<udid>" >&2; \
		exit 2; }
	@xcrun simctl boot "$(SIM_TARGET)" 2>/dev/null || true
	@open -a Simulator 2>/dev/null || true
	@$(RENDER_LOCAL_EXEC) env NO_PROXY="$(NO_PROXY_LIST)" no_proxy="$(NO_PROXY_LIST)" \
		uv run uvicorn src.api.app:app --host 127.0.0.1 --port 8000 &
	@for i in $$(seq 1 30); do \
		curl -fs --noproxy '*' --max-time 2 http://127.0.0.1:8000/api/v1/healthz >/dev/null 2>&1 && break; \
		sleep 1; \
	done; \
	curl -fs --noproxy '*' --max-time 2 http://127.0.0.1:8000/api/v1/healthz >/dev/null 2>&1 || \
		{ echo "ERROR: the API did not become healthy on :8000 within 30s." >&2; exit 1; }
	cd mobile && flutter run -d "$(SIM_TARGET)"

flutter-device: ## Run the Flutter app on a physical device against production.
	@$(PREFLIGHT) --label flutter-device $(PRE_FLUTTER) xcode cocoapods
	cd mobile && flutter run --dart-define=API_BASE_URL=https://ondoway.com/api/v1

flutter-device-profile: ## Run on a physical device in PROFILE mode (AOT, no JIT -- stable on iOS 26 where the debug JIT crashes; debug affordances stay visible via !kReleaseMode).
	@$(PREFLIGHT) --label flutter-device-profile $(PRE_FLUTTER) xcode cocoapods
	cd mobile && flutter run --profile --dart-define=API_BASE_URL=https://ondoway.com/api/v1

flutter-pub-get: ## Resolve Flutter dependencies.
	@$(PREFLIGHT) --label flutter-pub-get flutter
	cd mobile && NO_PROXY=pub.dev,*.pub.dev no_proxy=pub.dev,*.pub.dev flutter pub get

flutter-clean: ## Clean Flutter build artifacts and resolve dependencies.
	@$(PREFLIGHT) --label flutter-clean flutter
	cd mobile && flutter clean
	@$(MAKE) --no-print-directory flutter-pub-get

# ════════════════════════════════════════════════════════════════════════════
#  DATA AND PIPELINE
# ════════════════════════════════════════════════════════════════════════════
##@ DATA AND PIPELINE

survey-area-candidates: ## Survey undersized physical-extent POIs. Usage: make survey-area-candidates CITY=x.
	@$(PREFLIGHT) --label survey-area-candidates $(PRE_PY)
	@$(LOCAL_EXEC) uv run python scripts/survey_area_candidates.py --city "$(CITY)" $(ARGS)

fix-area-radii: ## Correct reviewed trigger radii locally. ARGS="--apply" writes.
	@$(PREFLIGHT) --label fix-area-radii $(PRE_PY)
	@$(LOCAL_EXEC) uv run python scripts/fix_area_trigger_radii.py --city "$(CITY)" $(ARGS)

backfill-provenance: ## Backfill beat provenance on the local dev graph.
	@$(PREFLIGHT) --label backfill-provenance uv python-deps db-dev
	@$(LOCAL_EXEC) uv run python -m scripts.upload_paris --provenance-only

backfill-poi-role: ## Apply reviewed POI roles. ARGS="--apply --neo4j" writes locally.
	@$(PREFLIGHT) --label backfill-poi-role $(PRE_PY) db-dev
	@$(LOCAL_EXEC) uv run python scripts/backfill_poi_role.py $(ARGS)

backfill-name-key: ## Backfill missing POI name keys on the local dev graph.
	@$(PREFLIGHT) --label backfill-name-key uv python-deps db-dev
	@$(LOCAL_EXEC) uv run python scripts/backfill_name_key.py $(ARGS)

wiki-fetch: ## Pin Wikipedia text. Usage: make wiki-fetch POI=x [TITLE=x] [CITY=paris].
	@$(PREFLIGHT) --label wiki-fetch $(PRE_PY)
	@$(LOCAL_EXEC) python3 scripts/wiki_fetch.py --city "$(or $(CITY),paris)" \
		--name "$(POI)"$(if $(TITLE), --title "$(TITLE)",)

gen-within-edges: ## Regenerate one city's within_edges.json.
	@$(PREFLIGHT) --label gen-within-edges $(PRE_PY)
	@$(LOCAL_EXEC) uv run python scripts/generate_within_edges.py --slug "$(or $(CITY),paris)"

tourability: ## Regenerate one city's tourability map. Asks the graph; ~5 minutes.
	@$(PREFLIGHT) --label tourability $(PRE_LOCAL_GRAPH)
	@$(LOCAL_EXEC) uv run python scripts/tourability_map.py --city-slug "$(or $(CITY),paris)"

validate-beats: ## Validate one city's committed beats before upload.
	@$(PREFLIGHT) --label validate-beats $(PRE_PY)
	@$(LOCAL_EXEC) uv run python scripts/validate_beats.py data/$(or $(CITY),paris)/beats.json

deploy: ## Deploy one city. Local by default; cloud requires TARGET=cloud CONFIRM_CLOUD_WRITE=1.
	@test -n "$(CITY)" || { echo "ERROR: CITY is required." >&2; exit 2; }
	@case "$(TARGET)" in \
		local) $(PREFLIGHT) --label "deploy TARGET=local" uv python-deps db-dev && \
			$(LOCAL_EXEC) uv run python -m scripts.deploy --slug "$(CITY)" ;; \
		cloud) test "$(CONFIRM_CLOUD_WRITE)" = "1" || \
			{ echo "ERROR: add CONFIRM_CLOUD_WRITE=1 for Aura." >&2; exit 2; }; \
			$(PREFLIGHT) --label "deploy TARGET=cloud" $(PRE_PY) render-key && \
			$(CLOUD_EXEC) uv run python -m scripts.aura_ensure_running && \
			$(CLOUD_EXEC) uv run python -m scripts.deploy --slug "$(CITY)" --allow-cloud ;; \
		*) echo "ERROR: TARGET must be local or cloud." >&2; exit 2 ;; \
	esac

prune-orphans: ## Find or delete orphaned records. Cloud requires explicit TARGET and confirmation.
	@test -n "$(CITY)" || { echo "ERROR: CITY is required." >&2; exit 2; }
	@case "$(TARGET)" in \
		local) $(PREFLIGHT) --label "prune-orphans TARGET=local" uv python-deps db-dev && \
			$(LOCAL_EXEC) uv run python -m scripts.prune_orphan_pois --slug "$(CITY)" \
				$(if $(filter 1,$(APPLY)),--apply,) ;; \
		cloud) test "$(CONFIRM_CLOUD_WRITE)" = "1" || \
			{ echo "ERROR: add CONFIRM_CLOUD_WRITE=1 for Aura." >&2; exit 2; }; \
			$(PREFLIGHT) --label "prune-orphans TARGET=cloud" $(PRE_PY) render-key && \
			$(CLOUD_EXEC) uv run python -m scripts.prune_orphan_pois --slug "$(CITY)" \
				--allow-cloud $(if $(filter 1,$(APPLY)),--apply,) ;; \
		*) echo "ERROR: TARGET must be local or cloud." >&2; exit 2 ;; \
	esac

fetch-boundary: ## Fetch an OSM boundary polygon.
	@$(PREFLIGHT) --label fetch-boundary $(PRE_PY)
	@$(LOCAL_EXEC) uv run python -m scripts.fetch_city_boundary --slug "$(SLUG)" \
		--relation "$(RELATION)"$(if $(filter 1,$(FORCE)), --force,)

geocode-pois: ## Geocode POIs through Nominatim.
	@$(PREFLIGHT) --label geocode-pois $(PRE_PY)
	@$(LOCAL_EXEC) uv run python -m scripts.geocode_pois --slug "$(SLUG)"$(if $(filter 1,$(ALL)), --all,)

# Prices how long a visitor spends AT each POI — outside, and inside where there
# IS an inside. Spends real model credits, so LIMIT= runs a subset and writes
# nothing; that is the intended way to inspect the output before a full pass.
# Needs the provider secrets, hence render-key + RENDER_LOCAL_EXEC, exactly like
# setup-audio.
poi-visit-duration: ## Price POI visit time. Usage: make poi-visit-duration SLUG=paris [LIMIT=10].
	@$(PREFLIGHT) --label poi-visit-duration $(PRE_PY) render-key
	@$(RENDER_LOCAL_EXEC) uv run python scripts/poi_visit_duration.py \
		--slug "$(or $(SLUG),paris)"$(if $(LIMIT), --limit $(LIMIT),) $(ARGS)

# Learns each POI's opening days and hours (redesign data row 6.1): one bulk
# OSM/Overpass query where OSM has the place, an audited model pass otherwise.
# Spends real model credits, so LIMIT= runs a subset and writes nothing —
# exactly the poi-visit-duration pattern, including render-key for the
# provider secrets.
poi-opening-hours: ## Learn POI opening hours. Usage: make poi-opening-hours SLUG=paris [LIMIT=10].
	@$(PREFLIGHT) --label poi-opening-hours $(PRE_PY) render-key
	@$(RENDER_LOCAL_EXEC) uv run python scripts/poi_opening_hours.py \
		--slug "$(or $(SLUG),paris)"$(if $(LIMIT), --limit $(LIMIT),) $(ARGS)

# Deterministic, $0, re-runnable at will (redesign data row 6.7): no model, no
# network, no credentials — name tokens, description and poi_role in, one of a
# closed twelve-word vocabulary out.
poi-place-category: ## Categorise every POI (deterministic, $0). Usage: make poi-place-category SLUG=paris.
	@$(PREFLIGHT) --label poi-place-category $(PRE_PY)
	@$(LOCAL_EXEC) uv run python scripts/poi_place_category.py \
		--slug "$(or $(SLUG),paris)" $(ARGS)

# The FOOTPRINT pass (Phase 7 S7.4; design §5.6): how far from the pin a walker is AT
# each place — `trigger_radius`, the field the audio placement rule reads. Deterministic,
# $0: the kind table over the records still at the uploader's default, the marquees and
# the eleven personas' stops reviewed by name with a basis sentence each.
poi-trigger-radius: ## Size every POI footprint by place kind ($0). Usage: make poi-trigger-radius SLUG=paris [ARGS=--dry-run].
	@$(PREFLIGHT) --label poi-trigger-radius $(PRE_PY)
	@$(LOCAL_EXEC) uv run python -m scripts.poi_trigger_radius \
		--slug "$(or $(SLUG),paris)" $(ARGS)

# The audit table for the pass above. Reads the committed JSON only -- no graph,
# no provider, no spend -- so it is safe to run at any time and is how a reviewer
# who has never been to the city checks the numbers.
poi-visit-report: ## Print the POI visit-capacity audit table. Usage: make poi-visit-report [SLUG=paris].
	@$(PREFLIGHT) --label poi-visit-report $(PRE_PY)
	@$(LOCAL_EXEC) uv run python scripts/report_visit_durations.py \
		--slug "$(or $(SLUG),paris)" $(ARGS)

# Toilets and benches (redesign data row 6.3): one bulk OSM/Overpass query,
# no model, no credentials, $0. Written to data/{slug}/body-places.json,
# never poi-raw.json — body places are not narrated places.
poi-body-places: ## Fetch toilets/benches from OSM ($0). Usage: make poi-body-places SLUG=paris [LIMIT=10].
	@$(PREFLIGHT) --label poi-body-places $(PRE_PY)
	@$(LOCAL_EXEC) uv run python -m scripts.poi_body_places \
		--slug "$(or $(SLUG),paris)"$(if $(LIMIT), --limit $(LIMIT),) $(ARGS)

# Three practical affordances per POI (redesign data row 6.4): can children
# run here, can two people sit and talk here, is it good to be left standing
# here after dark. The ONLY data row with no external source — reviewed at
# twice the normal rate. Spends real model credits, exactly the
# poi-visit-duration pattern.
poi-place-judgements: ## Judge child/talk/dark affordances. Usage: make poi-place-judgements SLUG=paris [LIMIT=10].
	@$(PREFLIGHT) --label poi-place-judgements $(PRE_PY) render-key
	@$(RENDER_LOCAL_EXEC) uv run python scripts/poi_place_judgements.py \
		--slug "$(or $(SLUG),paris)"$(if $(LIMIT), --limit $(LIMIT),) $(ARGS)

# The wait BEFORE entering, priced separately from visit time (redesign data
# row 6.5): class, peak/off-peak minutes, the peak hour bands, and the sentence
# arguing for them. Spends real model credits, so LIMIT= runs a subset and
# writes nothing — exactly the poi-visit-duration pattern, whose call door it
# imports. Review at the standard rate with named spot checks (Louvre,
# Sainte-Chapelle, Orsay, every `unpredictable`).
poi-queues: ## Price POI queues (row 6.5). Usage: make poi-queues SLUG=paris [LIMIT=10].
	@$(PREFLIGHT) --label poi-queues $(PRE_PY) render-key
	@$(RENDER_LOCAL_EXEC) uv run python scripts/poi_queues.py \
		--slug "$(or $(SLUG),paris)"$(if $(LIMIT), --limit $(LIMIT),) $(ARGS)

# The third manual performance of the same block became the template
# (CLAUDE.md §1.7): propagates every enriched field poi-raw.json carries into
# every data/{slug}/export/*.json chunk. $0, no network, no credentials.
sync-poi-exports: ## Sync enriched fields into export chunks. Usage: make sync-poi-exports SLUG=paris [ARGS=--check].
	@$(PREFLIGHT) --label sync-poi-exports $(PRE_PY)
	@$(LOCAL_EXEC) uv run python -m scripts.sync_poi_exports \
		--slug "$(or $(SLUG),paris)" $(ARGS)

# Who writes the transition sentences. scripts/tour_build.py now REFUSES to run
# without being told, because canned transitions printed under a "validation:
# PASS" header, with nothing naming them, is how part-fixed-string tours read as
# finished ones. --canned keeps this target free and unchanged in behaviour; it
# is stated rather than assumed. Override for real glue (spends money):
#   make tour-build GLUE=--haiku ARGS="..."
GLUE ?= --canned

tour-build: ## Build a local tour. ARGS="--start X --duration N"; GLUE=--haiku for real glue (paid).
	@test -n "$(ARGS)" || { echo 'ERROR: ARGS is required, e.g. ARGS="--start \"Pont Neuf\" --duration 60".' >&2; exit 2; }
	@if [ -n "$(findstring haiku,$(GLUE))" ]; then \
		$(PREFLIGHT) --label tour-build $(PRE_TOUR) render-key; \
	else \
		$(PREFLIGHT) --label tour-build $(PRE_TOUR); \
	fi
	@$(LOCAL_EXEC) uv run python scripts/tour_build.py $(GLUE) $(ARGS)

measure-planned-audio: ## Measure voiced audio against tier dwell.
	@$(PREFLIGHT) --label measure-planned-audio $(PRE_TOUR)
	@$(LOCAL_EXEC) uv run python scripts/measure_planned_audio.py $(ARGS)

measure-governor: ## Compare uncapped and governed emitted audio.
	@$(PREFLIGHT) --label measure-governor $(PRE_TOUR)
	@$(LOCAL_EXEC) uv run python scripts/measure_governor.py $(ARGS)

onboard-city: ## Onboard a fixture-backed city. Usage: make onboard-city CITY=x MODES=x.
	@$(PREFLIGHT) --label onboard-city $(PRE_PY) render-key
	@$(RENDER_LOCAL_EXEC) env ONBOARD_PROVIDER="$${ONBOARD_PROVIDER:?set ONBOARD_PROVIDER=anthropic (real) — there is no default}" \
		ONDOWAY_ONBOARD_HTTP="$${ONDOWAY_ONBOARD_HTTP:-fixture}" \
		ONBOARD_DATA_ROOT="$${ONBOARD_DATA_ROOT:-/tmp/ondoway-onboard}" \
		ONBOARD_REGISTRY_PATH="$${ONBOARD_REGISTRY_PATH:-/tmp/ondoway-onboard/cities.json}" \
		uv run python -m src.onboard.cli --city "$(CITY)" --modes "$(MODES)" \
		$(if $(filter 1,$(DRY_RUN)),--dry-run,) $(if $(filter 1,$(CONFIRM_COST)),--confirm-cost,)

# ════════════════════════════════════════════════════════════════════════════
#  DEPLOYMENT AND DIAGNOSTICS
# ════════════════════════════════════════════════════════════════════════════
##@ DEPLOYMENT AND DIAGNOSTICS

flutter-ipa: ## Build an IPA against the production API.
	@$(PREFLIGHT) --label flutter-ipa $(PRE_FLUTTER) xcode cocoapods
	cd mobile && flutter build ipa --dart-define=API_BASE_URL=https://ondoway.com/api/v1

testflight: ## Bump the build number, build the new IPA, then upload it.
	@$(PREFLIGHT) --label testflight $(PRE_PY) render-key $(PRE_FLUTTER) xcode cocoapods
	@$(RENDER_LOCAL_EXEC) bash -ceu '\
		test -n "$${APP_STORE_API_KEY_ID:-}" || { echo "ERROR: APP_STORE_API_KEY_ID missing." >&2; exit 2; }; \
		test -n "$${APP_STORE_ISSUER_ID:-}" || { echo "ERROR: APP_STORE_ISSUER_ID missing." >&2; exit 2; }; \
		cd mobile/ios && agvtool next-version -all; cd ../..; \
		$(MAKE) --no-print-directory flutter-ipa; \
		xcrun altool --upload-app --file mobile/build/ios/ipa/*.ipa --type ios \
			--apiKey "$$APP_STORE_API_KEY_ID" --apiIssuer "$$APP_STORE_ISSUER_ID"'

render-status: ## Show the latest Render deploy using the Keychain API credential.
	@$(PREFLIGHT) --label render-status $(PRE_PY) render-key
	@uv run python scripts/dev_env.py deploy-status

setup-audio: ## Validate audio providers using a fresh Render environment.
	@$(PREFLIGHT) --label setup-audio $(PRE_PY) render-key
	@$(RENDER_LOCAL_EXEC) uv run python scripts/check_audio_setup.py

aura-resume-proof: ## Explicitly test Aura resume and connectivity; never writes graph data.
	@$(PREFLIGHT) --label aura-resume-proof $(PRE_PY) render-key
	@$(CLOUD_EXEC) uv run python scripts/aura_resume_proof.py

flutter-test: ## Run Flutter tests with the project hang detector.
	@$(PREFLIGHT) --label flutter-test $(PRE_FLUTTER)
	@bash scripts/flutter_test.sh

clean: ## Remove build/test caches; never touch any database.
	@find . -type d -name __pycache__ -not -path '*/.venv/*' -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache 2>/dev/null || true
	@echo "✓ Cleared local caches. No database was touched."
