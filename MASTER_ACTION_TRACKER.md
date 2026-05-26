# MASTER ACTION TRACKER

> **Last updated:** Cycle 6 — 2026-05-26
>
> This file is the single source of truth for all remediation cycles on the
> `ml-incident-response-playbook` repository. Update it before and after every
> repair cycle. Do **not** rely solely on chat history for state.

---

## Architectural Invariants (must not regress)

| Invariant | Guard |
|---|---|
| All source code in `src/`, `api/`, `observability/`, `pipelines/`, `orchestration/` | CI Bandit + mypy scans these paths |
| All unit tests in `tests/unit/`; integration tests in `tests/integration/` | CI pytest path explicit |
| No hardcoded secrets; all injected via env / Secrets Manager | TruffleHog + CI secret guard |
| Terraform + K8s infra must agree on Redis auth injection method | Both use Secrets Manager (R-08, R-18) |
| `DriftSuiteResult`, `ThresholdResult`, `PsiResult` are frozen dataclasses | mypy strict enforces this |
| All actions SHA-pinned in CI | CI changelog tracks every bump |

---

## Cycle History

### Cycle 1 — Foundation Hardening

| ID | File | Fix | Commit | Status |
|---|---|---|---|---|
| OPEN-01 | `src/auth/key_store.py` | `KeyRotationStore` multi-key RS256 rotation | `47cddc0` | ✅ |
| ARCH-03/05 | `api/gdpr_routes.py` | Art.17 erasure + token revocation | `67c53e9` | ✅ |
| ETL-01 | `pipelines/etl_template.py` | Full extract/transform/load pipeline | `e0fc5f9` | ✅ |
| DAG-01 | `orchestration/ml_incident_dag.py` | Validate/detect/publish operators | `267a775` | ✅ |
| STRUCT-01 | repo root | Dir consolidation; tests → `tests/unit/` | `b9489a6` | ✅ |
| SEC-01 | `src/auth/key_store.py` | Hardcoded JWT secret removed; `cls.__new__` fix | `30148ef` | ✅ |

### Cycle 2 — Auth & Docs Repair

| ID | File | Fix | Commit | Status |
|---|---|---|---|---|
| AUTH-01 | `src/auth/key_store.py` | `sign_token()` missing `data` param name | `c2fc70d` | ✅ |
| DOCS-01 | `docs/CODE_OF_CONDUCT.md` | Symlink + dead nav entry removed | `18568a3` | ✅ |
| DOCS-02 | `docs/CODE_OF_CONDUCT.md` | `SECURITY.md` relative → absolute URL | `9fdcf09` | ✅ |

### Cycle 3 — Observability & Infrastructure

| ID | File | Fix | Commit | Status |
|---|---|---|---|---|
| R-01 | `observability/drift_check.py` | PSI + JSD + `check_drift_suite()` | `f83ffa5` | ✅ |
| R-02 | `observability/monitoring_example.py` | Prometheus metrics + `run_drift_check_job()` | `f83ffa5` | ✅ |
| R-03 | `observability/alert_rules.yml` | 14 Prometheus AlertManager rules | `f83ffa5` | ✅ |
| R-07 | `infrastructure/k8s-deployment.hardened.yml` | Prometheus port 8000→8080; readinessProbe path | `400a803` | ✅ |
| R-08 | `infrastructure/terraform/main.tf` | Redis auth_token via Secrets Manager; split REDIS_HOST/PORT | `4aa8523` | ✅ |
| R-10/R-11 | `tests/unit/test_drift_check.py` | 22 unit tests for drift_check | `400a803` | ✅ |
| R-12 | `tests/unit/test_monitoring_example.py` | 9 unit tests for monitoring_example | `400a803` | ✅ |
| R-14 | `CHANGELOG.md` | Keep-a-Changelog; CI-01→CI-43 full history | `400a803` | ✅ |
| R-15 | `.pre-commit-config.yaml` | ruff, mypy, bandit, detect-private-key | `f83ffa5` | ✅ |
| R-16 | `.editorconfig` | charset, indent, LF normalization | `f83ffa5` | ✅ |
| R-18 | `infrastructure/k8s-deployment.hardened.yml` | HPA (2–10 replicas) + Redis NetworkPolicy egress | `4aa8523` | ✅ |

### Cycle 4 — Test Coverage Completion & CI Scope Expansion

| ID | File | Fix | Commit | Status |
|---|---|---|---|---|
| R-05 | `tests/unit/test_anomaly_detection.py` | 24 unit tests for `simple_threshold` + `check_multiple` | `50c3b29` | ✅ |
| R-09 | `MASTER_ACTION_TRACKER.md` | Persistent in-repo tracker (this file) | `50c3b29` | ✅ |
| R-17 | `requirements.txt` | `prometheus-client==0.21.1` pinned as direct dep | `b2fa878` | ✅ |
| R-19 | `.github/workflows/secured_ci.yml` | Unit-tests job: add observability tests + `--cov=observability` | `b2fa878` | ✅ |

### Cycle 5 — ETL Test Correctness & Integration Audit

| ID | File | Fix | Commit | Status |
|---|---|---|---|---|
| R-06 | `tests/unit/test_etl_validation.py` | Rewrite: correct DB-shape rows for `load()`, mock SQLAlchemy engine, mock `run_pipeline()` I/O, remove duplicate anomaly/API blocks. 30 I/O-free tests. | `90c241f` | ✅ |
| R-13 | `tests/integration/` | Audited: 7 files confirmed (api_lifecycle, auth_lifecycle, cursor_pagination, incident_golden_path, logging_config, observability, repository_lifecycle). 40% gate intentional. | — (audit) | ✅ |

### Cycle 6 — CI Wiring Gap + CHANGELOG Backfill

| ID | File | Fix | Commit | Status |
|---|---|---|---|---|
| R-20 | `.github/workflows/secured_ci.yml` | `test_etl_validation.py` added to unit-tests `pytest` command (was silently skipped since `90c241f`) | this commit | ✅ |
| R-21 | `.github/workflows/secured_ci.yml` | `--cov=pipelines` added to unit-tests coverage scope; `--cov-fail-under` raised `65` → `68` | this commit | ✅ |
| R-22 | `CHANGELOG.md` | Backfill entries `[1.7.1]`–`[1.7.4]` for Cycles 4–6 commits not previously recorded | this commit | ✅ |
| R-23 | `MASTER_ACTION_TRACKER.md` | Cycle 6 table added; TD-01 note updated (CI-43 confirmed `0.63b0` on PyPI) | this commit | ✅ |

---

## Remaining Open Items

> ✅ All actionable remediation items are closed across Cycles 1–6.
> Remaining items are upstream-blocked tech debt only.
> No unresolved regressions. No correctness gaps. No silently-skipped tests.

---

## Tech Debt Registry

| ID | Package | Constraint | Root Cause | Resolution Path | Risk |
|---|---|---|---|---|---|
| TD-01 | `opentelemetry-*` | `0.63b0` (latest stable on PyPI as of 2026-05-26) | `instrumentation-fastapi 0.63b1` not yet released; `0.63b0` shipped CI-43 | Re-attempt when contrib publishes `0.63b1+` | Low |
| TD-02 | `starlette` | Capped at `0.49.1` | `fastapi==0.121.3` caps `starlette<0.51.0`; Dependabot PR-25 closed not-planned (CI-41) | Upgrade when `fastapi==0.122.x` ships | Low |
| TD-03 | `protobuf` | `<5.0` | OTel proto `1.27.0` constraint; CVE-2026-0994 accepted in `.trivyignore` | Resolves with TD-01 | Low |

---

## Test Coverage Matrix

| Module | Test File | # Tests | In CI `pytest`? |
|---|---|---|---|
| `src/services/` | `tests/test_incident_service.py` | ~20 | ✅ Yes |
| `src/domain/` + `src/schemas/` | `tests/test_incident_schema.py` | ~10 | ✅ Yes |
| `src/auth/key_store.py` | `tests/test_key_store.py` | ~15 | ✅ Yes |
| `src/incident_tracker/` | `tests/test_incident_tracker.py` | 17 | ✅ Yes |
| `observability/drift_check.py` | `tests/unit/test_drift_check.py` | 22 | ✅ Yes |
| `observability/monitoring_example.py` | `tests/unit/test_monitoring_example.py` | 9 | ✅ Yes |
| `observability/anomaly_detection.py` | `tests/unit/test_anomaly_detection.py` | 24 | ✅ Yes |
| `pipelines/etl_template.py` | `tests/unit/test_etl_validation.py` | 30 | ✅ Yes (R-20) |
| `api/` + `observability/` + `src/` | `tests/integration/` (7 files) | ~120 | ✅ Yes (integration job) |

**Total unit tests wired to CI:** ~150
**Total integration tests:** ~120

---

## CI Gate Order (current)

```
secrets-scan
  └─> dependency-audit ──┐
  └─> sast               ├──> unit-tests ──> integration-tests ──> container-scan ──> deploy-gate
```

**Coverage gates:**
- unit-tests: `--cov-fail-under=68` (src/ + observability/ + pipelines/)
- integration-tests: `--cov-fail-under=40` (api/ + observability/ + src/, Postgres live)

---

*Maintained by: strike-team-bot / remediation@mlops.zrl.dev*
