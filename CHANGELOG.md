# Changelog

All notable changes to this project are documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [Unreleased]

> R-GOD god-file decomposition complete (Cycle 9, 2026-05-26).
> Remaining open items: R-C03, R-C04, R-CI02 (now unblocked), plus upstream tech debt TD-01–TD-03.
> No unresolved regressions. No correctness gaps.

---

## [2.3.0] — 2026-05-26

### Refactored
- `R-GOD` (refactor): god-file decomposition of `api/app.py` (1 005 → 47 lines).
  Ten-step extraction across S1–S10, each verified by a standalone smoke test.
  New modules created:
  - `api/config.py` — env vars, algorithm guard, slowapi limiter, oauth2_scheme (S1, `ca7ea6e`)
  - `api/stub_users.py` — dev/test `_USERS` store + env guard (S2, `ca7ea6e`)
  - `api/schemas.py` — `Token`, `TokenPayload`, `IncidentCreate`, `StatusUpdate`, `IncidentUpdate` Pydantic models (S3, `d44af21`)
  - `src/auth/tokens.py` — `create_access_token`, `create_refresh_token`, `decode_token` JWT helpers; jti + ttl returned (S4, `a7c8cb6`)
  - `api/dependencies.py` — `authenticate_user`, `_record_login_failure`, `get_current_user`, `require_role`, `get_user_repo`, `get_denylist`; R-C03 marker applied to `_denylist`/`_user_repo` globals (S5, `47b9893`)
  - `api/lifespan.py` — `@asynccontextmanager lifespan` startup/shutdown wiring; DB check, user-repo wiring, Redis denylist init, RS256 key store, OTel bootstrap (S6, `16d2c1b`)
  - `api/routers/__init__.py` — package init (S7, `db8fd9f`)
  - `api/routers/health.py` — `GET /health` liveness + `GET /ready` readiness probes (S7, `db8fd9f` / `d702c12`)
  - `api/routers/auth.py` — `POST /auth/token`, `/auth/refresh`, `/auth/logout`; rate-limited 5/min (S8, `12e7969`)
  - `api/routers/incidents.py` — `POST/GET /incidents/`, `GET/PATCH /incidents/{id}`, `PATCH /incidents/{id}/status`; R-C07 TOCTOU fix carried through (S9, `247ef19`)
  - `api/middleware.py` — `trace_and_security_headers` moved from `app.py` inline (S10 fix, `fef50e8`)
  - `api/app.py` — slimmed to 47-line factory shell (S10, `257a9de` / `fef50e8`)

  Unlocked:
  - **R-C03** shared-state race (`_denylist`/`_user_repo` now isolated)
  - **R-C04** `_build_engine()` import-time construction (token helpers importable without DB)
  - **R-CI02** clean `api.app:app` Gunicorn/Uvicorn entry point

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
  Commit: `90c241f`.

---

## [1.7.1] — 2026-05-26

### Added
- `R-05` (feat test): `tests/unit/test_anomaly_detection.py` — 24 unit tests. Commit: `50c3b29`.
- `R-09` (chore docs): `MASTER_ACTION_TRACKER.md` created as persistent in-repo tracker.
- `R-17` (fix deps): `prometheus-client==0.21.1` pinned as explicit direct dependency. Commit: `b2fa878`.

### Fixed
- `R-19` / `CI-44` (fix ci): expand unit-tests job to cover `observability/` package. Commit: `b2fa878`.

---

## [1.7.0] — 2026-05-26

### Fixed
- `CI-44` (feat test): expand unit-tests CI job to cover `observability/` package.
  Raised `--cov-fail-under` `60` → `65`.

---

## [1.6.9] — 2026-05-26

### Fixed
- `CI-43` (fix deps): bump `opentelemetry-instrumentation-fastapi` `0.62b0` → `0.63b0`.

---

## [1.6.8] — 2026-05-26

### Fixed
- `CI-42` (fix deps): align `opentelemetry-api` / `instrumentation-fastapi` to sdk `1.42.1` / `0.62b0`.

---

## [1.6.7] — 2026-05-25

### Changed
- `CI-41` (chore ci): closed Dependabot PR-25 (`starlette==0.52.1`) as not-planned. Tracked as TD-02.

---

## [1.6.6] — 2026-05-25

### Changed
- `CI-40` (chore deps): bump `opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-grpc` `1.27.0` → `1.42.1`.

---

## [1.6.5] — 2026-05-25

### Fixed
- `CI-39` (chore deps): bump `asyncpg` `0.30.0` → `0.31.0`. Resolves `GHSA-7f4w-j353-w3mg`.

---

## [1.6.4] — 2026-05-25

### Fixed
- `CI-38` (fix ci): declare all evaluated jobs in `deploy-gate` `needs` array.

---

## [1.6.3] — 2026-05-25

### Fixed
- `CI-37` (fix ci): add `test_incident_tracker.py` to unit-tests pytest command.

---

## [1.6.2] — 2026-05-25

### Fixed
- `CI-36` (fix ci): replace hardcoded deploy-gate strings with `needs.<job>.result` expressions.

---

## [1.6.1] — 2026-05-25

### Fixed
- `CI-35` (fix ci): replace unresolvable checkout SHA with `actions/checkout@v4.3.0` tag.

---

## [1.6.0] — 2026-05-25

### Changed
- `CI-34` (chore ci): bump all action SHAs to Node 24-compatible versions.

---

## [1.5.5] — 2026-05-25

### Fixed
- `CI-33` (fix test): use sentinel `_UNSET` in `_service_with_mock_repo`.

---

## [1.5.4] — 2026-05-25

### Fixed
- `CI-32` (fix sast): cast `RSAPrivateKey`/`RSAPublicKey` in `key_store.py`.

---

## [1.5.3] — 2026-05-25

### Fixed
- `CI-31` (fix ci): seed `semgrep.sarif` before semgrep step.

---

## [1.5.2] — 2026-05-25

### Changed
- `CI-30` (chore docs): point `site_url` and Docs badge to `mlops.zrl.dev`.

---

## [1.5.1] — 2026-05-25

### Fixed
- `CI-29` (fix docs): resolve 10 MkDocs strict-mode warnings.

---

## [1.5.0] — 2026-05-25

### Added
- `CI-28` (feat docs): MkDocs Material site + GitHub Pages deploy workflow.

---

## [1.4.0] — 2026-05-25

### Added
- `CI-27` (feat test): `unit-tests` job (SQLite, no Postgres). `--cov-fail-under=60`.

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
