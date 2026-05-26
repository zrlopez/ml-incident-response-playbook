# Changelog

All notable changes to this project are documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [Unreleased]

### Added
- `observability/drift_check.py` — full PSI + Jensen-Shannon divergence feature drift implementation ([R-01])
- `observability/monitoring_example.py` — Prometheus + OTel + drift/anomaly reference integration ([R-02])
- `observability/alert_rules.yml` — 14 production Prometheus AlertManager rules across 7 groups ([R-03])
- `tests/unit/test_drift_check.py` — 22 unit tests for drift detection layer ([R-10/R-11])
- `tests/unit/test_monitoring_example.py` — 9 unit tests for monitoring integration ([R-12])
- `.pre-commit-config.yaml` — ruff, mypy, bandit, TruffleHog, and hygiene hooks ([R-15])
- `.editorconfig` — charset, indent, and line-ending normalization across file types ([R-16])

### Fixed
- `infrastructure/k8s-deployment.hardened.yml`: Prometheus scrape port annotation corrected 8000 → 8080 ([R-07])

---

## [1.6.9] — 2026-05-26

### Fixed
- `CI-43` (fix deps): bump `opentelemetry-instrumentation-fastapi` 0.62b0 → 0.63b0.
  Root cause: `sdk 1.42.1` hard-requires `semantic-conventions==0.63b1`;
  `instrumentation-fastapi 0.62b0` hard-requires `semantic-conventions==0.62b0`.
  Pip cannot satisfy both `==` pins simultaneously → `ResolutionImpossible`.

## [1.6.8] — 2026-05-26

### Fixed
- `CI-42` (fix deps): align `opentelemetry-api/instrumentation-fastapi` to sdk `1.42.1` / `0.62b0`.
  Root cause: `api` was left at `1.27.0` when CI-40 bumped sdk to `1.42.1`.

## [1.6.7] — 2026-05-25

### Changed
- `CI-41` (chore ci): closed Dependabot PR-25 (`starlette==0.52.1`) as not-planned.
  `fastapi==0.121.3` caps `starlette<0.51.0`; bump irresolvable without paired fastapi upgrade.
  Tracked as tech debt pending `fastapi 0.122.x`.

## [1.6.6] — 2026-05-25

### Changed
- `CI-40` (chore deps): bump `opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-grpc` `1.27.0` → `1.42.1`.

## [1.6.5] — 2026-05-25

### Fixed
- `CI-39` (chore deps): bump `asyncpg` `0.30.0` → `0.31.0`; resolves `GHSA-7f4w-j353-w3mg`.

## [1.6.4] — 2026-05-25

### Fixed
- `CI-38` (fix ci): declare all evaluated jobs in `deploy-gate` needs array.
  GitHub Actions only populates `needs.<job>.result` for direct needs entries.

## [1.6.3] — 2026-05-25

### Fixed
- `CI-37` (fix ci): add `test_incident_tracker.py` to unit-tests pytest command;
  add `src/incident_tracker` to `--cov` scope.

## [1.6.2] — 2026-05-25

### Fixed
- `CI-36` (fix ci): replace hardcoded deploy-gate strings with `needs.<job>.result` expressions;
  add `exit 1` on any gate failure.

## [1.6.1] — 2026-05-25

### Fixed
- `CI-35` (fix ci): replace unresolvable checkout SHA `ef36d109...` with `actions/checkout@v4.3.0` tag.

## [1.6.0] — 2026-05-25

### Changed
- `CI-34` (chore ci): bump all action SHAs to Node 24-compatible versions.
  `checkout v4.2.2→v4.3.0`, `codeql v3.28.15→v4.36.0`, `docker/setup-buildx v3.10.0→v4.1.0`,
  `docker/build-push v5.4.0→v7.2.0`, `dependency-review v4.5.0→v5.0.0`,
  `sbom-action v0.18.0→v0.24.0`, `upload-pages-artifact v3.0.1→v5.0.0`,
  `deploy-pages v4.0.5→v5.0.0`.

## [1.5.5] — 2026-05-25

### Fixed
- `CI-33` (fix test): use sentinel `_UNSET` in `_service_with_mock_repo`.

## [1.5.4] — 2026-05-25

### Fixed
- `CI-32` (fix sast): cast `RSAPrivateKey`/`RSAPublicKey` in `key_store.py`;
  move `semgrep.sarif` seed to top of sast job.

## [1.5.3] — 2026-05-25

### Fixed
- `CI-31` (fix ci): seed `semgrep.sarif` before semgrep step;
  add `continue-on-error: true` for missing `SEMGREP_APP_TOKEN`.

## [1.5.2] — 2026-05-25

### Changed
- `CI-30` (chore docs): point `site_url` and Docs badge to `mlops.zrl.dev`.

## [1.5.1] — 2026-05-25

### Fixed
- `CI-29` (fix docs): resolve 10 MkDocs strict-mode warnings;
  `CONTRIBUTING.md` absolute URLs; `onboarding.md` path fixes.

## [1.5.0] — 2026-05-25

### Added
- `CI-28` (feat docs): MkDocs Material site + GitHub Pages deploy workflow.
  `docs.yml`: build + deploy on push to main. `mermaid-render.yml` added.

## [1.4.0] — 2026-05-25

### Added
- `CI-27` (feat test): `unit-tests` job (SQLite, no Postgres).
  Covers `test_incident_service.py`, `test_incident_schema.py`, `test_key_store.py`,
  `test_incident_tracker.py` with `--cov-fail-under=60`.

## [1.3.0] — 2026-05-25

### Changed
- `CI-26` (refactor ci): replace SQLite test job with `integration-tests` as gate.

## [1.2.8] — 2026-05-24

### Fixed
- `CI-25` (fix ci): set SARIF scan exit-code to 0; table scan is the hard gate.

## [1.2.7] — 2026-05-24

### Fixed
- `CI-24` (fix ci): wire `.trivyignore`; add diagnostic table run.

## [1.2.6] — 2026-05-24

### Fixed
- `CI-23` (fix ci): bump `trivy-action` `v0.31.0` → `v0.36.0`.

## [1.2.5] — 2026-05-24

### Fixed
- `CI-22` (fix ci): seed Trivy SARIF; decouple `container-scan` from `integration-tests`.

## [1.2.4] — 2026-05-24

### Fixed
- `CI-21` (fix ci): SHA-pin all 6 remaining actions via API.

## [1.2.0] — 2026-05-24

### Added
- `CI-17` (feat ci): SHA-pin all actions; add Trivy gate, pip-audit, secret guard.

## [1.1.14] — 2026-05-24

### Fixed
- `CI-16` (fix ci): pin `codeql/upload-sarif` back to `v3`.

## [1.1.13] — 2026-05-24

### Fixed
- `CI-15` (fix ci): correct TruffleHog on push trigger.

## [1.1.0] — 2026-05-23

### Added
- `CI-02` (feat ci): split jobs, add Postgres service, coverage, scope.

## [1.0.0] — 2026-05-23

### Added
- `CI-01` (feat ci): initial hardened CI/CD pipeline.
  JWT secret, mypy gate, Trivy, SBOM, Bandit.
