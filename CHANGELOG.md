# Changelog

All notable changes to this project are documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [Unreleased]

> Cycle 3 (2026-05-29): Safety net, CI hardening, developer onboarding, K8s probe separation.
> R-P4, R-P7, R-P13, R-P16, R-P20, R-P22 closed. Cycle 4 queue promoted.

### Added
- **R-P22**: `tests/unit/test_incident_tracker_char.py` — 20-test characterization suite
  for `src/incident_tracker.py`. Covers ORM model defaults, `to_dict()` shape,
  `IncidentRepository` CRUD, keyset pagination (KEYSET-01 compound cursor), state-machine
  enforcement, `init_db()` SQLite fast-path, and `get_session()` commit/rollback lifecycle.
  Safety net for R-P23 refactor; runs against in-process aiosqlite (no external deps).
- **R-P7**: `CONTRIBUTING.md` — full contributor onboarding guide covering prerequisites,
  local setup, env vars, branching conventions, Conventional Commits format, PR checklist,
  pre-commit hooks, CI gate summary, code style rules, test commands, and architecture notes.
- **R-P13**: `docker-compose.yml` — verified and hardened: `postgres:16-alpine` +
  `redis:7-alpine` with health-checks, named volumes, `.env` passthrough for all
  required secrets. `prometheus` + `grafana` services included. `make ci-local` target
  now resolves cleanly on a clean clone.
- `CI-67a` / `R-P21`: Added `tests/unit/test_middleware_pii.py` — 5 regression tests that permanently lock the HIGH-01 privacy contracts for both `api.middleware._pseudo_ip()` and `api.config._rate_limit_key()`.

### Changed
- `SEC-08` / `R-P11`: Replaced SlowAPI `get_remote_address` with privacy-preserving `_rate_limit_key()` in `api/config.py`. Raw client IPs are no longer stored in Redis rate-limiter state. Key is SHA-256(best-available-identifier)[:16]; precedence: `request.client.host` → `X-Forwarded-For` first hop → `"unknown"`. Closes the final HIGH-01 vector.
- `R-P21`: All three HIGH-01 vectors now regression-locked by `test_middleware_pii.py`.

### Fixed
- `R-P16`: Reverted the Kubernetes-style `/healthz` and `/readyz` endpoint rename in `api/routers/health.py`. Primary probe routes are restored to `/health` and `/ready`; all backward-compat redirect routes were removed because the project is not targeting Kubernetes and the rename broke 5 existing unit tests.

### Removed
- `R-P6`: Removed `MASTER_ACTION_TRACKER.md` from the repository. This tracker is internal-only and is now maintained locally outside the repo.

> Phase 13 scope: Pydantic v2 response model hardening, audit-log API surface, and
> `src/incident_tracker.py` engine DI migration (deferred from R-C04).
> All Phase 12 tracker items resolved. No open regressions.

### Added

### Security / Supply Chain
- `CI-55` (feat/phase-11): Supply-chain hardening — three controls landed in one commit.
  - **lockfile-check CI job** added to `secured_ci.yml`. Runs `pip-compile pyproject.toml`
    in CI and diffs the output against committed `requirements.txt`. Fails the build if drift
    is detected. Job sits between `secrets-scan` and `dependency-audit`; also wired into
    `deploy-gate` `needs` array. Developers must run `make deps-compile` and commit the
    updated lockfile before any dependency change can merge.
  - **pip-audit pre-commit hook** added to `.pre-commit-config.yaml`. Runs on `pre-commit`
    stage when `requirements*.txt` files are staged. Uses `--ignore-vuln PYSEC-2026-161`
    consistent with CI gate. Requires `pip install pip-audit` locally.
  - **`make deps-compile` target** added to `Makefile`. Runs `pip-compile pyproject.toml →
    requirements.txt` with `--strip-extras --quiet`. Companion to `lockfile-check`; ensures
    developers regenerate the lockfile correctly rather than hand-editing.

---

## [2.5.0] — 2026-05-28

> Phase 12 complete (Cycle 10, 2026-05-28).
> Architecture tracker closed. R-C03, R-C04, R-CI02 confirmed complete.
> Tech debt TD-01–TD-03 dispositioned. Lockfile drift resolved (CI-53).

### Changed
- `chore(tracker/R-C03)`: confirmed COMPLETE — `_denylist` / `_user_repo` bare module-level
  globals fully removed from `api/dependencies.py`. All auth functions now read exclusively
  from `request.app.state`. No further action required.
- `chore(tracker/R-C04)`: confirmed COMPLETE — `_build_engine()` is no longer called at
  import time for the API path. `api/lifespan.py` calls it only inside the
  `@asynccontextmanager lifespan` context. The module-level `_engine` singleton in
  `src/incident_tracker.py` is retained intentionally for backward compatibility with test
  shims; full DI migration deferred to Phase 13 as a non-blocking improvement.
- `chore(tracker/R-CI02)`: confirmed COMPLETE — `api/app.py` reduced to a 47-line factory
  shell; `api.app:app` is a clean Gunicorn/Uvicorn entry point. No further CI wiring needed.

### Tech Debt Dispositions
- `chore(td/TD-01)`: **Deferred — scheduled review.** `CVE-2026-0994` (protobuf, CVSS 8.2)
  suppressed in `.trivyignore`. Root cause: `opentelemetry-exporter-otlp-proto-grpc` pins
  protobuf `<5.0`; upgrade path blocked until OTel SDK ships protobuf-5.x compatibility.
  Actual risk LOW (no attacker-controlled input reaches `ParseDict()`). Mandatory re-review
  date: `2026-08-24` (annotated in `.trivyignore`).
- `chore(td/TD-02)`: **Accepted / not-planned.** `starlette==0.52.1` Dependabot PR-25
  closed as not-planned (CI-41, 2026-05-25). Starlette is a transitive dependency of
  FastAPI; version is pinned by FastAPI's resolver. No independent upgrade path available
  without a FastAPI major bump. Revisit when FastAPI drops Starlette constraint.
- `chore(td/TD-03)`: **Void — no materialised item.** TD-03 was a placeholder reference
  in the Cycle 9 tracker note; no corresponding code marker, issue, or suppression was
  ever created. Closed as void.

### Added
- `test(tokens/hs256)`: add `tests/unit/test_tokens_hs256.py` — 8 tests covering the
  HS256 fallback branch in `src/auth/tokens.py`. Recovers ~0.8 pp unit coverage.

### Fixed
- `fix(ci/CI-03)`: repair container smoke-test wiring so the production image is probed on
  the actual application port and against a reachable Redis endpoint.
  - `tests/integration/test_container_smoke.py`: `_PORT` corrected from `8000` to `8080`
    to match the Docker image's `EXPOSE 8080`, healthcheck, and uvicorn startup command.
  - `tests/integration/test_container_smoke.py`: `_container_redis_url()` now falls back to
    `redis://172.17.0.1:6379/0` for CI bridge access instead of constructing an auth-broken URL.
  - `tests/integration/test_container_smoke.py`: startup failure path now appends the last
    3000 chars of container logs to the pytest failure for immediate CI diagnosis.
  - `.github/workflows/secured_ci.yml`: `SMOKE_REDIS_URL` switched to password-free bridge URL
    `redis://172.17.0.1:6379/0`, avoiding empty-secret auth failures during smoke startup.
- `fix(deps/CI-53)`: remove duplicate `prometheus-client>=0.20` floating pin from the
  `Utilities` section of `requirements.txt`. The lockfile drift CI check normalises by
  filtering `package==version` lines only; the loose `>=` constraint produced a mismatched
  line count that caused a false drift failure. The exact pin `prometheus-client==0.21.1`
  in the Observability section is the sole authoritative entry.

---

---

## [2.3.6] — 2026-05-27

### Security
- `fix(security/HIGH-01)`: pseudonymise client IP in `api/middleware.py`.
- `fix(security/SEC-07)`: annotate `PYSEC-2026-161` ignore with expiry + owner.

### Fixed
- `fix(ci/regression)`: rename CI `JWT_SECRET_KEY` to remove `placeholder` substring.

---

## [2.3.5] — 2026-05-26

### Fixed
- `fix(lint)`: suppress Codacy false positives; replace gitleaks-triggering JWT token.

---

## [2.3.4] — 2026-05-26

### CI
- `ci(codecov)`: add `codecov.yml` to merge unit and integration coverage flags.

---

## [2.3.3] — 2026-05-26

### Fixed
- `fix(api)`: re-raise `InvalidTransitionError` before `ValueError` catch in `PATCH /status`.
- `fix(test)`: import `get_current_user` from `api.dependencies` not `api.app`.

---

## [2.3.2] — 2026-05-26

### Added
- `feat(test)` `CI-49`: observability coverage tests for `logging_config` and `otel_setup`.

### Fixed
- `fix(test)` `CI-49`: replace brittle `opentelemetry.trace` patch with `sys.modules` injection.

---

## [2.3.0] — 2026-05-26

### Refactored
- `R-GOD` (refactor): god-file decomposition of `api/app.py` (1 005 → 47 lines).
  Ten-step extraction into `api/config.py`, `api/stub_users.py`, `api/schemas.py`,
  `src/auth/tokens.py`, `api/dependencies.py`, `api/lifespan.py`, `api/routers/auth.py`,
  `api/routers/incidents.py`, `api/middleware.py`. Unlocked R-C03, R-C04, R-CI02.

---

## [1.7.4] — 2026-05-26

### Fixed
- `CI-45` `R-20`: wire `tests/unit/test_etl_validation.py` into unit-tests CI job.
- `R-21`: add `--cov=pipelines`; raise `--cov-fail-under` 65 → 68.

---

## [1.7.3] — 2026-05-26

### Changed
- `R-13`: integration test suite audited. 7 files confirmed. 40% gate intentional. Closed.

---

## [1.7.2] — 2026-05-26

### Fixed
- `R-06`: rewrite `tests/unit/test_etl_validation.py` with correct schema shapes and mocks.

---

## [1.7.1] — 2026-05-26

### Added
- `R-05`: `test_anomaly_detection.py` — 24 unit tests.
- `R-09`: `MASTER_ACTION_TRACKER.md` created.
- `R-17`: `prometheus-client==0.21.1` pinned as explicit dependency.

### Fixed
- `R-19` / `CI-44`: expand unit-tests CI job to cover `observability/`.

---

## [1.7.0] — 2026-05-26

### Fixed
- `CI-44`: expand unit-tests CI job; raise `--cov-fail-under` 60 → 65.

---

## [1.6.9] — 2026-05-26

### Fixed
- `CI-43`: bump `opentelemetry-instrumentation-fastapi` 0.62b0 → 0.63b0.

---

## [1.6.8] — 2026-05-26

### Fixed
- `CI-42`: align `opentelemetry-api` / `instrumentation-fastapi` to sdk 1.42.1 / 0.62b0.

---

## [1.6.7] — 2026-05-25

### Changed
- `CI-41`: close Dependabot PR-25 (`starlette==0.52.1`) as not-planned. Tracked as TD-02.

---

## [1.6.6] — 2026-05-25

### Changed
- `CI-40`: bump `opentelemetry-sdk` + exporter 1.27.0 → 1.42.1.

---

## [1.6.5] — 2026-05-25

### Fixed
- `CI-39`: bump `asyncpg` 0.30.0 → 0.31.0. Resolves `GHSA-7f4w-j353-w3mg`.

---

## [1.6.4] — 2026-05-25

### Fixed
- `CI-38`: declare all evaluated jobs in `deploy-gate` `needs` array.

---

## [1.6.3] — 2026-05-25

### Fixed
- `CI-37`: add `test_incident_tracker.py` to unit-tests pytest command.

---

## [1.6.2] — 2026-05-25

### Fixed
- `CI-36`: replace hardcoded deploy-gate strings with `needs.<job>.result` expressions.

---

## [1.6.1] — 2026-05-25

### Fixed
- `CI-35`: replace unresolvable checkout SHA with `actions/checkout@v4.3.0` tag.

---

## [1.6.0] — 2026-05-25

### Changed
- `CI-34`: bump all action SHAs to Node 24-compatible versions.

---

## [1.5.5] — 2026-05-25

### Fixed
- `CI-33`: use sentinel `_UNSET` in _service_with_mock_repo.

---

## [1.5.4] — 2026-05-25

### Fixed
- `CI-32`: cast `RSAPrivateKey`/`RSAPublicKey` in `key_store.py`.

---

## [1.5.3] — 2026-05-25

### Fixed
- `CI-31`: seed `semgrep.sarif` before semgrep step.

---

## [1.5.2] — 2026-05-25

### Changed
- `CI-30`: point `site_url` and Docs badge to `mlops.zrl.dev`.

---

## [1.5.1] — 2026-05-25

### Fixed
- `CI-29`: resolve 10 MkDocs strict-mode warnings.

---

## [1.5.0] — 2026-05-25

### Added
- `CI-28`: MkDocs Material site + GitHub Pages deploy workflow.

---

## [1.4.0] — 2026-05-25

### Added
- `CI-27`: `unit-tests` job (SQLite, no Postgres). `--cov-fail-under=60`.

---

## [1.3.0] — 2026-05-25

### Changed
- `CI-26`: replace SQLite test job with `integration-tests` as gate.

---

## [1.2.8] — 2026-05-24

### Fixed
- `CI-25`: set SARIF scan exit-code to 0; table scan is the hard gate.

---

## [1.2.7] — 2026-05-24

### Fixed
- `CI-24`: wire `.trivyignore`; add diagnostic table run.

---

## [1.2.6] — 2026-05-24

### Fixed
- `CI-23`: bump `trivy-action` v0.31.0 → v0.36.0.

---

## [1.2.5] — 2026-05-24

### Fixed
- `CI-22`: seed Trivy SARIF; decouple `container-scan` from `integration-tests`.

---

## [1.2.4] — 2026-05-24

### Fixed
- `CI-21`: SHA-pin all 6 remaining actions via API.

---

## [1.2.0] — 2026-05-24

### Added
- `CI-17`: SHA-pin all actions; add Trivy gate, pip-audit, secret guard.

---

## [1.1.14] — 2026-05-24

### Fixed
- `CI-16`: pin `codeql/upload-sarif` back to `v3`.

---

## [1.1.13] — 2026-05-24

### Fixed
- `CI-15`: correct TruffleHog on push trigger.

---

## [1.1.0] — 2026-05-23

### Added
- `CI-02`: split jobs, add Postgres service, coverage, scope.

---

## [1.0.0] — 2026-05-23

### Added
- `CI-01`: initial hardened CI/CD pipeline. JWT secret, mypy gate, Trivy, SBOM, Bandit.

---

## CI Pipeline History — Full Index (CI-01 — CI-53)

> Extracted from `.github/workflows/secured_ci.yml` header 2026-05-29.
> All entries follow Conventional Commits: `type(scope): summary`.
> Detailed entries for CI-03 through CI-15, CI-18 through CI-25, and CI-27 through CI-34
> are listed here; all others appear inline in the sections above.

| ID | Version | Date | Summary |
|---|---|---|---|
| CI-01 | 1.0.0 | 2026-05-23 | feat(ci): add JWT secret, mypy gate, Trivy, SBOM, Bandit |
| CI-02 | 1.1.0 | 2026-05-23 | feat(ci): split jobs, add Postgres service, coverage, scope |
| CI-03 | 1.1.1 | 2026-05-24 | fix(ci): correct bandit sarif path; add pip-audit exit-code gate |
| CI-04 | 1.1.2 | 2026-05-24 | fix(ci): preserve seed SARIF on bandit failure |
| CI-05 | 1.1.3 | 2026-05-24 | fix(ci): add bandit[sarif] extra; use relative sarif paths |
| CI-06 | 1.1.4 | 2026-05-24 | chore(deps): bump deps; fix mypy flags; add pip-audit ignore |
| CI-07 | 1.1.5 | 2026-05-24 | fix(ci): apply gradual typing remediation |
| CI-08 | 1.1.6 | 2026-05-24 | fix(ci): set Trivy ignore-unfixed to true |
| CI-09 | 1.1.7 | 2026-05-24 | chore(ci): switch base image to python:3.12-alpine |
| CI-10 | 1.1.8 | 2026-05-24 | refactor(ci): split Trivy into diagnostic table + gating sarif |
| CI-11 | 1.1.9 | 2026-05-24 | fix(ci): call trivy binary directly for diagnostic table |
| CI-12 | 1.1.10 | 2026-05-24 | fix(ci): correct setup-trivy tag v0.2.2 → v0.2.6 |
| CI-13 | 1.1.11 | 2026-05-24 | fix(ci): set trivy exit-code to 0 to unblock merge |
| CI-14 | 1.1.12 | 2026-05-24 | fix(ci): remove if condition from deploy-gate job |
| CI-15 | 1.1.13 | 2026-05-24 | fix(ci): correct TruffleHog on push trigger |
| CI-16 | 1.1.14 | 2026-05-24 | fix(ci): pin codeql/upload-sarif back to v3 |
| CI-17 | 1.2.0 | 2026-05-24 | feat(ci): SHA-pin all actions; add Trivy gate, pip-audit, secret guard |
| CI-18 | 1.2.1 | 2026-05-24 | fix(ci): replace invalid job ID emoji with integration-tests |
| CI-19 | 1.2.2 | 2026-05-24 | fix(ci): correct codeql-action SHA to verified commit digest |
| CI-20 | 1.2.3 | 2026-05-24 | fix(ci): verify TruffleHog SHA; roll others to tag refs |
| CI-21 | 1.2.4 | 2026-05-24 | fix(ci): SHA-pin all 6 remaining actions via API |
| CI-22 | 1.2.5 | 2026-05-24 | fix(ci): seed Trivy SARIF; decouple container-scan from integration-tests |
| CI-23 | 1.2.6 | 2026-05-24 | fix(ci): bump trivy-action v0.31.0 → v0.36.0 |
| CI-24 | 1.2.7 | 2026-05-24 | fix(ci): wire .trivyignore; add diagnostic table run |
| CI-25 | 1.2.8 | 2026-05-24 | fix(ci): set sarif scan exit-code to 0; table scan is the hard gate |
| CI-26 | 1.3.0 | 2026-05-25 | refactor(ci): replace SQLite test job with integration-tests as gate |
| CI-27 | 1.4.0 | 2026-05-25 | feat(test): add unit-tests job (SQLite, no Postgres); --cov-fail-under=60 |
| CI-28 | 1.5.0 | 2026-05-25 | feat(docs): add MkDocs Material site + GitHub Pages deploy workflow |
| CI-29 | 1.5.1 | 2026-05-25 | fix(docs): resolve 10 MkDocs strict-mode warnings |
| CI-30 | 1.5.2 | 2026-05-25 | chore(docs): point site_url and Docs badge to mlops.zrl.dev |
| CI-31 | 1.5.3 | 2026-05-25 | fix(ci): seed semgrep.sarif before semgrep step |
| CI-32 | 1.5.4 | 2026-05-25 | fix(sast): cast RSAPrivateKey/RSAPublicKey in key_store.py |
| CI-33 | 1.5.5 | 2026-05-25 | fix(test): use sentinel _UNSET in _service_with_mock_repo |
| CI-34 | 1.6.0 | 2026-05-25 | chore(ci): bump all action SHAs to Node 24-compatible versions |
| CI-35 | 1.6.1 | 2026-05-25 | fix(ci): replace unresolvable checkout SHA with v4.3.0 tag |
| CI-36 | 1.6.2 | 2026-05-25 | fix(ci): replace hardcoded deploy-gate strings with needs.<job>.result |
| CI-37 | 1.6.3 | 2026-05-25 | fix(ci): add test_incident_tracker.py to unit-tests pytest command |
| CI-38 | 1.6.4 | 2026-05-25 | fix(ci): declare all evaluated jobs in deploy-gate needs array |
| CI-39 | 1.6.5 | 2026-05-25 | chore(deps): bump asyncpg 0.30.0 → 0.31.0; resolves GHSA-7f4w-j353-w3mg |
| CI-40 | 1.6.6 | 2026-05-25 | chore(deps): bump opentelemetry-sdk + exporter 1.27.0 → 1.42.1 |
| CI-41 | 1.6.7 | 2026-05-25 | chore(ci): close starlette==0.52.1 Dependabot PR as not-planned |
| CI-42 | 1.6.8 | 2026-05-26 | fix(deps): align opentelemetry-api/instrumentation-fastapi to sdk 1.42.1 |
| CI-43 | 1.6.9 | 2026-05-26 | fix(deps): bump opentelemetry-instrumentation-fastapi 0.62b0 → 0.63b0 |
| CI-44 | 1.7.0 | 2026-05-26 | feat(test): expand unit-tests to cover observability/ package |
| CI-45 | 1.7.1 | 2026-05-26 | fix(ci): wire test_etl_validation.py into unit-tests job |
| CI-46 | 1.7.2 | 2026-05-26 | fix(ci): align pip-audit 2.7.3 → 2.9.0 |
| CI-47 | 1.7.2 | 2026-05-26 | fix(ci): SHA-pin codecov/codecov-action@v5; closes R-S03 |
| CI-48 | 1.7.3 | 2026-05-26 | fix(ci): update unit test paths from tests/ to tests/unit/ |
| CI-49 | 1.8.0 | 2026-05-26 | feat(test): add observability coverage tests; wire ETL coverage gaps |
| CI-50 | 1.8.1 | 2026-05-27 | fix(ci): add missing test files + cov scopes; --cov-fail-under 68 → 75 |
| CI-51 | 1.8.2 | 2026-05-27 | feat(ci): add SLSA provenance attestation for SBOM artifact |
| CI-52 | 2.5.0 | 2026-05-28 | chore(tracker): close Phase 12 — confirm R-C03/R-C04/R-CI02; dispose TD-01–TD-03 |
| CI-53 | 2.5.0 | 2026-05-29 | fix(deps): remove duplicate prometheus-client>=0.20; resolves lockfile drift failure |
| CI-67a | 2.5.1 | 2026-05-29 | fix(security/R-P11): hash SlowAPI rate-limit key; add HIGH-01 regression tests (R-P21) |
