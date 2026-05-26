# Changelog

All notable changes to this project are documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [Unreleased]

> All actionable remediation items closed across Cycles 1–6.
> Remaining open items are upstream-blocked tech debt (TD-01, TD-02, TD-03).
> No unresolved regressions. No correctness gaps.

---

## [1.7.4] — 2026-05-26

### Fixed
- `CI-45` `R-20` (fix ci): wire `tests/unit/test_etl_validation.py` into unit-tests `pytest` command.
  Root cause: R-06 (`90c241f`) wrote 30 ETL tests but never added the file to the CI pytest
  invocation — tests were silently skipped on every run since that commit.
- `R-21` (fix ci): add `--cov=pipelines` to unit-tests coverage scope.
  `pipelines/etl_template.py` had zero coverage credit despite 30 tests exercising it.
  `--cov-fail-under` raised `65` → `68` to account for `pipelines/` now being measured.
  Closes R-20 + R-21. Bumps CI changelog to CI-45.

---

## [1.7.3] — 2026-05-26

### Changed
- `R-13` (chore test): integration test suite audited.
  7 files confirmed present: `test_api_lifecycle_http.py`, `test_auth_lifecycle.py`,
  `test_cursor_pagination.py`, `test_incident_golden_path.py`, `test_logging_config.py`,
  `test_observability.py`, `test_repository_lifecycle.py`.
  40% Postgres coverage gate confirmed intentional. Closed as by-design.
- `MASTER_ACTION_TRACKER.md`: Cycle 5 recorded; R-06 + R-13 closed (`d6c73f2`).

---

## [1.7.2] — 2026-05-26

### Fixed
- `R-06` (fix test): rewrite `tests/unit/test_etl_validation.py` with correct schema shapes and mocks.
  Root causes: `TestLoad` passed raw ETL input rows to `load()` which expects post-transform
  DB-shape rows; `TestRunPipeline` called `run_pipeline()` bare (live SQLAlchemy + SQLite);
  duplicate `TestSimpleThreshold`, `TestCheckMultiple`, `TestIncidentCreate` blocks removed.
  Replaced with 30 I/O-free tests across 8 classes: `TestValidateRow` (12),
  `TestParseTimestamp` (7), `TestComputeRunId` (2), `TestHashPii` (3),
  `TestInferEventType` (6), `TestTransform` (7), `TestLoad` (3), `TestRunPipeline` (3).
  Commit: `90c241f`.

---

## [1.7.1] — 2026-05-26

### Added
- `R-05` (feat test): `tests/unit/test_anomaly_detection.py` — 24 unit tests for
  `simple_threshold` + `check_multiple` in `observability/anomaly_detection.py`.
  Tests: high/low/within-range breach detection, signed `pct_deviation`, zero-baseline
  `ValueError`, `pct`-range `ValueError`, `check_low=False` flag, multi-metric batch,
  all-breached / all-clear paths, empty dict no-op, `ThresholdResult` field validation.
  Zero I/O dependencies. Commit: `50c3b29`.
- `R-09` (chore docs): `MASTER_ACTION_TRACKER.md` created as persistent in-repo tracker.
  Replaces ephemeral chat state. Full Cycle 1–4 history with per-item status + commit SHA.
- `R-17` (fix deps): `prometheus-client==0.21.1` pinned as explicit direct dependency.
  Previously only a transitive dep of `prometheus-fastapi-instrumentator`.
  `monitoring_example.py` imports it directly; must be pinned for supply-chain hygiene.
  Commit: `b2fa878`.

### Fixed
- `R-19` / `CI-44` (fix ci): expand unit-tests job to cover `observability/` package.
  Added `tests/unit/test_drift_check.py`, `test_monitoring_example.py`,
  `test_anomaly_detection.py` to `pytest` command.
  Added `--cov=observability` to coverage scope.
  Raised `--cov-fail-under` `60` → `65`. Commit: `b2fa878`.

---

## [1.7.0] — 2026-05-26

### Fixed
- `CI-44` (feat test): expand unit-tests CI job to cover `observability/` package.
  Added `tests/unit/test_drift_check.py`, `test_monitoring_example.py`,
  `test_anomaly_detection.py` to pytest command.
  Added `--cov=observability` to unit-tests coverage scope.
  Raised `--cov-fail-under` `60` → `65` (observability modules now measured).
  Closes R-19. Paired with R-17 `prometheus-client==0.21.1` direct pin.

---

## [1.6.9] — 2026-05-26

### Fixed
- `CI-43` (fix deps): bump `opentelemetry-instrumentation-fastapi` `0.62b0` → `0.63b0`.
  Root cause: `sdk 1.42.1` hard-requires `semantic-conventions==0.63b1`;
  `instrumentation-fastapi 0.62b0` hard-requires `semantic-conventions==0.62b0`.
  Pip cannot satisfy both `==` pins simultaneously → `ResolutionImpossible`.
  `0.63b0` confirmed on PyPI 2026-05-26.

---

## [1.6.8] — 2026-05-26

### Fixed
- `CI-42` (fix deps): align `opentelemetry-api` / `instrumentation-fastapi` to sdk `1.42.1` / `0.62b0`.
  Root cause: `api` was left at `1.27.0` when CI-40 bumped sdk to `1.42.1`.
  sdk hard-requires `api==<same version>`; `ResolutionImpossible` in dep-audit + SAST.
  SARIF upload errors were pure downstream cascade.

---

## [1.6.7] — 2026-05-25

### Changed
- `CI-41` (chore ci): closed Dependabot PR-25 (`starlette==0.52.1`) as not-planned.
  `fastapi==0.121.3` caps `starlette<0.51.0`; bump irresolvable without paired fastapi upgrade.
  Tracked as tech debt (TD-02) pending `fastapi 0.122.x`.

---

## [1.6.6] — 2026-05-25

### Changed
- `CI-40` (chore deps): bump `opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-grpc`
  `1.27.0` → `1.42.1`. Resolves upstream OTel compatibility gap.

---

## [1.6.5] — 2026-05-25

### Fixed
- `CI-39` (chore deps): bump `asyncpg` `0.30.0` → `0.31.0`. Resolves `GHSA-7f4w-j353-w3mg`.
  No API changes vs `0.30.x`.

---

## [1.6.4] — 2026-05-25

### Fixed
- `CI-38` (fix ci): declare all evaluated jobs in `deploy-gate` `needs` array.
  GitHub Actions only populates `needs.<job>.result` for direct needs entries;
  ancestor jobs not listed evaluate to `null`, causing gate to exit 1 on every run.

---

## [1.6.3] — 2026-05-25

### Fixed
- `CI-37` (fix ci): add `test_incident_tracker.py` to unit-tests pytest command;
  add `src/incident_tracker` to `--cov` scope.
  17 unit tests were unreachable; deploy-gate exited 1 on every push to main.

---

## [1.6.2] — 2026-05-25

### Fixed
- `CI-36` (fix ci): replace hardcoded deploy-gate strings with `needs.<job>.result` expressions;
  add `exit 1` on any gate failure.
  Root cause: deploy-gate previously reported "passed" regardless of outcome (F-01).

---

## [1.6.1] — 2026-05-25

### Fixed
- `CI-35` (fix ci): replace unresolvable checkout SHA `ef36d109...` with `actions/checkout@v4.3.0` tag.
  7 live uses updated.

---

## [1.6.0] — 2026-05-25

### Changed
- `CI-34` (chore ci): bump all action SHAs to Node 24-compatible versions.
  `checkout v4.2.2→v4.3.0`, `codeql v3.28.15→v4.36.0`, `docker/setup-buildx v3.10.0→v4.1.0`,
  `docker/build-push v5.4.0→v7.2.0`, `dependency-review v4.5.0→v5.0.0`,
  `sbom-action v0.18.0→v0.24.0`, `upload-pages-artifact v3.0.1→v5.0.0`,
  `deploy-pages v4.0.5→v5.0.0`.

---

## [1.5.5] — 2026-05-25

### Fixed
- `CI-33` (fix test): use sentinel `_UNSET` in `_service_with_mock_repo`
  so `get_return=None` is correctly assigned.

---

## [1.5.4] — 2026-05-25

### Fixed
- `CI-32` (fix sast): cast `RSAPrivateKey`/`RSAPublicKey` in `key_store.py`;
  move `semgrep.sarif` seed to top of sast job.

---

## [1.5.3] — 2026-05-25

### Fixed
- `CI-31` (fix ci): seed `semgrep.sarif` before semgrep step;
  add `continue-on-error: true` for missing `SEMGREP_APP_TOKEN`.

---

## [1.5.2] — 2026-05-25

### Changed
- `CI-30` (chore docs): point `site_url` and Docs badge to `mlops.zrl.dev`.
  Custom domain `mlops.zrl.dev` → `zrlopez.github.io` (DNS verified).

---

## [1.5.1] — 2026-05-25

### Fixed
- `CI-29` (fix docs): resolve 10 MkDocs strict-mode warnings.
  `CONTRIBUTING.md` absolute URLs; `onboarding.md` path fixes.

---

## [1.5.0] — 2026-05-25

### Added
- `CI-28` (feat docs): MkDocs Material site + GitHub Pages deploy workflow.
  `docs.yml`: build + deploy on push to main. `mermaid-render.yml` added.

---

## [1.4.0] — 2026-05-25

### Added
- `CI-27` (feat test): `unit-tests` job (SQLite, no Postgres).
  Covers `test_incident_service.py`, `test_incident_schema.py`, `test_key_store.py`
  with `--cov-fail-under=60`. Gate order: sast/dep-audit → unit-tests → integration-tests.

---

## [1.3.0] — 2026-05-25

### Changed
- `CI-26` (refactor ci): replace SQLite test job with `integration-tests` as gate.

---

## [1.2.8] — 2026-05-24

### Fixed
- `CI-25` (fix ci): set SARIF scan exit-code to 0; table scan is the hard gate.

---

## [1.2.7] — 2026-05-24

### Fixed
- `CI-24` (fix ci): wire `.trivyignore`; add diagnostic table run.

---

## [1.2.6] — 2026-05-24

### Fixed
- `CI-23` (fix ci): bump `trivy-action` `v0.31.0` → `v0.36.0`.

---

## [1.2.5] — 2026-05-24

### Fixed
- `CI-22` (fix ci): seed Trivy SARIF; decouple `container-scan` from `integration-tests`.

---

## [1.2.4] — 2026-05-24

### Fixed
- `CI-21` (fix ci): SHA-pin all 6 remaining actions via API.

---

## [1.2.0] — 2026-05-24

### Added
- `CI-17` (feat ci): SHA-pin all actions; add Trivy gate, pip-audit, secret guard.

---

## [1.1.14] — 2026-05-24

### Fixed
- `CI-16` (fix ci): pin `codeql/upload-sarif` back to `v3`.

---

## [1.1.13] — 2026-05-24

### Fixed
- `CI-15` (fix ci): correct TruffleHog on push trigger.

---

## [1.1.0] — 2026-05-23

### Added
- `CI-02` (feat ci): split jobs, add Postgres service, coverage, scope.

---

## [1.0.0] — 2026-05-23

### Added
- `CI-01` (feat ci): initial hardened CI/CD pipeline.
  JWT secret, mypy gate, Trivy, SBOM, Bandit.
