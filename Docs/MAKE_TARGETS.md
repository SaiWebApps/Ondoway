# Make target contract

Make is the supported command boundary. “Prerequisites” below means work the
target performs or provisions itself; callers do not need to run those commands
first. “Render” means a fresh, complete service-environment fetch for that
process—never a cached or filtered subset.

| Target | Self-provisioned prerequisites | Fresh Render | Standalone contract |
|---|---|---:|---|
| `help` | None | No | Yes |
| `doctor` | None; reports every requirement, starts nothing | No | Yes |
| `setup` | Alias for `bootstrap`; the name most people reach for first | No | Yes |
| `preflight` | Checks every requirement and repairs what is repairable | No | Yes |
| `preflight-list` | None; describes the requirement vocabulary | No | Yes |
| `bootstrap` | Python/Flutter deps, all local DBs, dev data, Playwright browser, and the routing MAP DATA (not the engine: its first start builds tiles for over an hour, so a routing target starts it) | No | Yes, with host toolchain |
| `render-auth-setup` | Interactive macOS Keychain entry | API auth only | Yes |
| `render-auth-status` | Keychain lookup and service access check | API auth only | Yes |
| `config-status` | Three exact local-profile assertions | Yes | Yes |
| `sync` | None | No | Yes |
| `sync-apple` | Apple mirror reachability and lockfile backup | No | Yes, on Apple network |
| `requirements` | None | No | Yes |
| `lint` | None | No | Yes |
| `format` | None | No | Yes |
| `flutter-analyze` | None | No | Yes, with Flutter |
| `test` | Every database, data, Valhalla, browser/live/cloud shard | Where required | Yes; definitive suite, five concurrent tracks |
| `audit` | Lint and `test` | Where required | Yes |
| `test-file` | Test/dev DBs, dev data, Valhalla | Only with `LIVE=1` | Yes, with `FILE` |
| `test-live` | Render auth, test/dev DBs, dev data, Valhalla | Yes | Yes |
| `test-workbench` | Test/workbench DBs, Valhalla; its API takes a free port | No; the shard pins mock providers | Yes; one test drives the real tour pipeline (hence Valhalla), still on mock providers |
| `golden-probe` | Test/dev DBs, dev data, Valhalla | No | Yes |
| `golden-diff` | Dev DB/data and Valhalla | No | Yes, with `FIXTURE` |
| `score-saved-tours` | None; reads `data/{city}/tours/` only | No | Yes; $0, no DB or container |
| `score-gold-text` | None; reads the quality standard only | No | Yes; $0, no DB or container |
| `score-human-tours` | None; reads `fixtures/reference-tours/` only | No | Yes; $0, no DB or container |
| `tour-batch-plan` | Dev DB/data and Valhalla | No | Yes |
| `tour-batch-live` | Dev DB/data and Valhalla | Yes | Yes, with approved hash |
| `tour-batch-review-plan` | Dev DB/data and Valhalla | No | Yes |
| `tour-batch-review-live` | Dev DB/data and Valhalla | Yes | Yes, with approved hash |
| `db-up` | Selected Docker service | No | Yes, `DB=dev\|test\|workbench` |
| `db-down` | Selected Docker service resolution | No | Yes |
| `db-status` | None | No | Yes |
| `db-reset` | Exact selected service and volume | No | Yes; local only |
| `db-parity` | Dev DB, or cloud read session | Cloud only | Yes |
| `valhalla-up` | Main-checkout compose path and health wait | No | Yes |
| `valhalla-down` | Main-checkout compose path | No | Yes |
| `valhalla-status` | None | No | Yes |
| `valhalla-build-tiles` | Destination directory | No | Yes, with network/disk |
| `api` | Dev DB/data, Valhalla, :8000 | Yes | Yes; foreground server |
| `workbench` | Dev DB/data, Valhalla, :8000 (reuses a healthy API rather than restarting it) | Yes | Yes; foreground server |
| `dashboard` | Dev DB/data | No | Yes; foreground server |
| `flutter-web` | None | No | Yes, with Flutter/Chrome |
| `flutter-ios` | Dev DB/data, simulator boot, local API | Yes | Yes, with Xcode |
| `flutter-device` | None | No | Yes, with attached device |
| `flutter-pub-get` | None | No | Yes |
| `flutter-clean` | Flutter clean and dependency resolution | No | Yes |
| `survey-area-candidates` | Exact local profile | No | Yes, with `CITY` |
| `fix-area-radii` | Exact local profile | No | Yes, with `CITY` |
| `backfill-provenance` | Dev DB | No | Yes |
| `backfill-poi-role` | Exact local profile | No | Yes |
| `backfill-name-key` | Dev DB | No | Yes |
| `wiki-fetch` | Exact local profile | No | Yes, with `POI` |
| `gen-within-edges` | Exact local profile | No | Yes |
| `validate-beats` | Exact local profile | No | Yes |
| `deploy` | Dev DB, or explicit Aura resume | Cloud only | Yes, with `CITY`; cloud requires confirmation |
| `prune-orphans` | Dev DB, or explicit cloud selection | Cloud only | Yes, with `CITY`; deletes only with `APPLY=1` |
| `fetch-boundary` | Exact local profile | No | Yes, with slug/relation |
| `geocode-pois` | Exact local profile | No | Yes, with slug |
| `poi-opening-hours` | None; reads/writes `data/{slug}/poi-raw.json` | Yes | Yes; two Overpass queries (the map first, as OpenStreetMap text) + a paid model pass for the doors the map does not hold; `LIMIT=` writes nothing |
| `poi-place-category` | None; reads/writes `data/{slug}/poi-raw.json` | No | Yes; deterministic and $0, no network |
| `poi-body-places` | None; writes `data/{slug}/body-places.json` | No | Yes; one Overpass query, $0, no model; `LIMIT=` writes nothing |
| `poi-place-judgements` | None; reads/writes `data/{slug}/poi-raw.json` | Yes | Yes; a paid model pass; `LIMIT=` writes nothing |
| `sync-poi-exports` | None; reads `poi-raw.json`, writes `data/{slug}/export/*.json` | No | Yes; $0, no network; `ARGS=--check` writes nothing |
| `tour-build` | Dev DB/data and Valhalla | No | Yes |
| `measure-planned-audio` | Dev DB/data | No | Yes |
| `measure-governor` | Dev DB/data | No | Yes |
| `onboard-city` | Isolated temporary data and registry defaults | No | Yes, with city/modes |
| `flutter-ipa` | None | No | Yes, with signing toolchain |
| `testflight` | Credential validation, build-number bump, then fresh IPA | Yes | Yes, with App Store credentials |
| `render-status` | Keychain API auth | API auth only | Yes |
| `setup-audio` | Exact local profile | Yes | Yes |
| `aura-resume-proof` | Explicit cloud profile | Yes | Yes; no graph writes |
| `flutter-test` | Project hang detector | No | Yes |
| `clean` | None | No | Yes; no databases |
| `_track-pure` | Test/dev DBs, dev data, Valhalla | No | Internal track: DB-free pytest, 8 workers |
| `_track-db` | All three test DBs, dev data, Valhalla | No | Internal track: needs_db pytest on 7688/7690/7691 |
| `_track-db-live` | Delegates to `_track-db` then `test-live` | Via test-live | Internal track chain |
| `_track-devgraph` | Test/dev DBs, dev data, Valhalla | No | Internal track: the three 7687-corpus pytest files |
| `_track-surfaces` | Delegates to `flutter-test`, `test-workbench`, `_test-cloud` | Via delegates | Internal track chain |
| `_track-tour-quality` | Delegates to golden, grade, invariants | No | Internal track chain |
| `_test-golden` | Test/dev DBs, dev data, Valhalla | No | Internal shard |
| `_test-grade` | Test/dev DBs, dev data, Valhalla | No | Internal shard |
| `_test-invariants` | Test/dev DBs, dev data, Valhalla | No | Internal shard |
| `_test-cloud` | Render auth and Aura read session | Yes | Internal read-only shard |

Cloud safety is structural: no pytest command receives the cloud profile,
`db-reset` accepts only three fixed local services, and cloud data mutations
require `TARGET=cloud CONFIRM_CLOUD_WRITE=1`.

**Switch variables take the literal `1`, and nothing else.** `APPLY`, `FORCE`,
`ALL`, `DRY_RUN` and `CONFIRM_COST` are armed by `APPLY=1`, never by `APPLY=true`
or `APPLY=yes` — those now read as off. Make's `$(if ...)` tests for a non-empty
value, so `APPLY=0` and `APPLY=false` previously ARMED the flag: the command a
careful person types to preview a cloud prune deleted records instead. Erring
towards off is the deliberate trade; a mistyped switch does nothing rather than
something irreversible.
