# MASTER ACTION TRACKER

> **Repository:** `zrlopez/ml-incident-response-playbook`
> **Last updated:** 2026-05-26 — Cycle 9 complete (R-GOD)
> **HEAD at close of Cycle 9:** `fef50e8`

---

## Legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Completed and pushed |
| 🔴 | Open blocker — CI will fail |
| 🟡 | Medium risk — degraded behaviour |
| 🟢 | Low / cosmetic |
| ⏸️ | Upstream-blocked tech debt |

---

## Cycle 1 — Foundation (2026-05-23/24)

| ID | File | Fix | Status |
|----|------|-----|--------|
| OPEN-01 | `src/incident_tracker.py` | Add `IncidentTracker` class stub | ✅ |
| ARCH-03 | `src/auth/jwt_rs256.py` | RS256 key rotation logic | ✅ |
| ARCH-05 | `src/config.py` | Pydantic settings + env validation | ✅ |
| ETL-01 | `pipelines/etl_template.py` | ETL scaffold with schema validation | ✅ |
| DAG-01 | `orchestration/dag_definition.py` | Airflow DAG skeleton | ✅ |
| STRUCT-01 | `src/`, `api/`, `tests/` dirs | Directory structure normalised | ✅ |
| SEC-01 | `src/auth/jwt_rs256.py` | Remove hardcoded JWT secret | ✅ |

## Cycle 2 — Auth + Docs (2026-05-24)

| ID | File | Fix | Status |
|----|------|-----|--------|
| AUTH-01 | `src/auth/jwt_rs256.py` | Fix `sign_token()` parameter name | ✅ |
| DOCS-01 | `mkdocs.yml` | Remove dead nav entries + symlink | ✅ |
| DOCS-02 | `SECURITY.md` | Fix disclosure URL | ✅ |

## Cycle 3 — Observability + Infra (2026-05-24)

| ID | File | Fix | Status |
|----|------|-----|--------|
| R-01 | `observability/drift_check.py` | PSI drift detection implementation | ✅ |
| R-02 | `observability/monitoring_example.py` | Prometheus metric exports | ✅ |
| R-03 | `observability/anomaly_detection.py` | IQR + z-score anomaly detection | ✅ |
| R-07 | `infrastructure/terraform/` | Terraform Redis auth + encryption | ✅ |
| R-08 | `infrastructure/k8s/` | K8s Redis secret injection | ✅ |
| R-10 | `observability/prometheus_rules.yml` | Alert rules for drift + anomaly | ✅ |
| R-11 | `.pre-commit-config.yaml` | Pre-commit hooks (ruff, mypy, bandit) | ✅ |
| R-12 | `.editorconfig` | EditorConfig for consistent formatting | ✅ |
| R-14 | `configs/logging_config.py` | Structured JSON logging | ✅ |
| R-15 | `configs/settings.py` | Central settings with Pydantic | ✅ |
| R-16 | `api/middleware.py` | Request ID + timing middleware | ✅ |
| R-18 | `infrastructure/k8s/redis-secret.yaml` | Secrets Manager ARN reference | ✅ |

## Cycle 4 — Tests + CI (2026-05-25)

| ID | File | Fix | Status |
|----|------|-----|--------|
| R-05 | `tests/unit/test_anomaly_detection.py` | 24 anomaly unit tests | ✅ |
| R-09 | `MASTER_ACTION_TRACKER.md` | Tracker initialised | ✅ |
| R-17 | `requirements.txt` | `prometheus-client==0.21.1` direct pin | ✅ |
| R-19 | `.github/workflows/secured_ci.yml` | Observability added to CI unit-tests job | ✅ |

## Cycle 5 — ETL Tests (2026-05-25)

| ID | File | Fix | Status |
|----|------|-----|--------|
| R-06 | `tests/unit/test_etl_validation.py` | 30 ETL validation tests | ✅ |
| R-13 | `tests/integration/` | Integration suite audit confirmed clean | ✅ |

## Cycle 6 — Wiring + CHANGELOG (2026-05-26)

| ID | File | Fix | Status |
|----|------|-----|--------|
| R-20 | `.github/workflows/secured_ci.yml` | Wire `test_etl_validation.py` into CI | ✅ |
| R-21 | `.github/workflows/secured_ci.yml` | Add `--cov=pipelines` to unit-tests scope | ✅ |
| R-22 | `.github/workflows/secured_ci.yml` | Raise `--cov-fail-under` 65→68 | ✅ |
| R-23 | `CHANGELOG.md` | Backfill entries through `[1.7.4]` | ✅ |

## Cycle 7 — Config Alignment (2026-05-26)

| ID | File | Issue | Fix | Status |
|----|------|-------|-----|--------|
| R-24 | `pyproject.toml` | `python_version=3.12` / `ruff target=py312` mismatched to CI 3.11; `fail_under=85` vs gate 68 | Align all three to 3.11/68 | ✅ |
| R-25 | `Makefile` | `lint` used `flake8`; `--cov-fail-under=85`; `typecheck` missing `observability/` `pipelines/`; `-m unit` marker silently skipped most tests | Replace `flake8`→`ruff check`; fix gate; expand paths; drop marker | ✅ |
| R-26 | `.github/workflows/secured_ci.yml` | `pip-audit==2.7.3` in CI vs `2.9.0` in `requirements-dev.txt` | Align CI to `2.9.0` | ✅ |
| R-27 | `Dockerfile` | `pip==24.3.1` outdated; SHA-pin TODO never completed | Bump pip to `25.1.1`; add SHA-pin note | ✅ |

## Cycle 8 — Docs / Env / Compose Audit (2026-05-26)

| ID | File | Issue | Fix | Status |
|----|------|-------|-----|--------|
| R-28 | `README.md` | Stale coverage gates 60%/40% vs actual 68%/40% | Updated to ≥68% unit | ✅ |
| R-29 | `README.md` | Roadmap listed two completed items | Removed completed items | ✅ |
| R-30 | `README.md` | `docker run -p 8000:8000` vs image on 8080 | Fixed to `-p 8080:8080` | ✅ |
| R-31 | `README.md` | PR checklist referenced non-existent `REMEDIATION_LOG.md` | Replaced with `MASTER_ACTION_TRACKER.md` | ✅ |
| R-32 | `.env.example` | Missing `REDIS_URL`; `JWT_ALGORITHM` undocumented; no HS256 fallback warning | Added all three; RS256 primary labelled | ✅ |
| R-33 | `docker-compose.yml` | `redis_data:` volume missing `driver: local`; Redis missing auth healthcheck | Added `driver: local`; healthcheck uses `AUTH` | ✅ |
| R-34 | `docker-compose.prod.yml` | Port mapping `8080:8080` should be `8000:8080` (host:container); Redis missing auth healthcheck | Fixed port; added healthcheck | ✅ |
| R-35 | `docker-compose.yml` | OTEL collector image `0.115.0` in CVE range `<0.116.0` | Bumped to `0.116.0` | ✅ |
| R-36 | `docker-compose.yml` | Jaeger `1.62.0` outdated vs `1.65.0` stable | Bumped to `1.65.0` | ✅ |
| R-37 | `api/app.py` | `ALLOWED_ORIGINS` not validated — accepted any string | Added Pydantic `AnyHttpUrl` validator in `api/config.py` | ✅ |
| R-38 | `src/auth/password.py` | `bcrypt` work-factor 10; OWASP 2024 recommends ≥12 | Raised to 12 | ✅ |
| R-39 | `src/auth/jwt_rs256.py` | `aud` claim not validated on decode | Added `audience` parameter to `decode()` | ✅ |
| R-40 | `api/app.py` | `REFRESH_TOKEN_EXPIRE_DAYS` default 7d not documented | Added to `.env.example` with comment | ✅ |
| R-41 | `api/app.py` | `LOGIN_FAILURE_THRESHOLD` / `LOGIN_FAILURE_WINDOW_SECONDS` not in `.env.example` | Added with safe defaults | ✅ |
| R-42 | `src/incident_tracker.py` | `InvalidTransitionError` not exported from package `__init__` | Added to `src/__init__.py` | ✅ |
| R-43 | `tests/conftest.py` | `ALLOWED_ORIGINS` env var missing in test env → `ValidationError` on import | Added `ALLOWED_ORIGINS=http://localhost:3000` to `monkeypatch.setenv` block | ✅ |
| R-44 | `.github/workflows/secured_ci.yml` | `ALLOWED_ORIGINS` missing from CI `env:` block | Added | ✅ |
| R-45 | `CHANGELOG.md` | Not updated since `[1.7.4]` | Backfilled Cycle 7–8 entries | ✅ |

## Cycle 9 — R-GOD God-File Decomposition (2026-05-26)

| ID | File | Description | Commit | Status |
|----|------|-------------|--------|--------|
| R-GOD-S1 | `api/config.py` | Extract env vars, algorithm guard, limiter, `oauth2_scheme` | `ca7ea6e` | ✅ |
| R-GOD-S2 | `api/stub_users.py` | Extract dev `_USERS` store + env guard | `ca7ea6e` | ✅ |
| R-GOD-S3 | `api/schemas.py` | Extract `Token`, `TokenPayload`, `IncidentCreate`, `StatusUpdate`, `IncidentUpdate` | `d44af21` | ✅ |
| R-GOD-S4 | `src/auth/tokens.py` | Extract `create_access_token`, `create_refresh_token`, `decode_token`; jti + ttl returned | `a7c8cb6` | ✅ |
| R-GOD-S5 | `api/dependencies.py` | Extract `authenticate_user`, `get_current_user`, `require_role`, `_record_login_failure`, globals; R-C03 marker | `47b9893` | ✅ |
| R-GOD-S6 | `api/lifespan.py` | Extract FastAPI lifespan context manager; DB/Redis/OTel startup wiring | `16d2c1b` | ✅ |
| R-GOD-S7 | `api/routers/health.py` | Extract `GET /health`, `GET /ready` probes | `d702c12` | ✅ |
| R-GOD-S8 | `api/routers/auth.py` | Extract `POST /auth/token`, `/auth/refresh`, `/auth/logout` | `12e7969` | ✅ |
| R-GOD-S9 | `api/routers/incidents.py` | Extract all 5 incident routes | `247ef19` | ✅ |
| R-GOD-S10 | `api/app.py` | Slim to 47-line factory shell; move `trace_and_security_headers` to `middleware.py` | `fef50e8` | ✅ |

---

## Open Items — Cycle 10 Candidates

### 🟡 Architecture Debt (Now Unblocked by R-GOD)

| ID | File | Issue | Priority |
|----|------|-------|----------|
| R-C03 | `api/dependencies.py` | `_denylist`/`_user_repo` are bare module-level globals — shared-state race on worker restart. Migrate to `app.state` reads. | 🟡 High |
| R-C04 | `src/incident_tracker.py` | `_build_engine()` called at module import time. Confirmed import chain: `dependencies.py → repository → incident_tracker → _build_engine()`. Refactor to DI / lazy init. | 🟡 High |
| R-CI02 | `Dockerfile` / CI | Gunicorn/Uvicorn entry point should use `api.app:app` explicitly. Validate in Dockerfile `CMD`. | 🟡 Medium |

### 🟢 Lower Priority

| ID | File | Issue | Priority |
|----|------|-------|----------|
| R-C05 | `src/incident_tracker.py` | `IncidentTracker` god-class — `open_incident`, `transition_status`, `update_metadata`, `list_open`, `get_incident` all in one class | 🟢 Low |
| R-C06 | `api/routers/incidents.py` | `update_metadata` pre-checks partially duplicated in route + service | 🟢 Low |
| R-C07 | `api/routers/incidents.py` | `update_status` carries TOCTOU comment — confirm removal is complete end-to-end | 🟢 Low |
| R-C08 | `src/services/incident_service.py` | No explicit rollback on partial update failure | 🟢 Low |
| R-C09 | `api/dependencies.py` | `_record_login_failure` extracted but not yet covered by unit test | 🟢 Low |

### ⏸️ Upstream-Blocked Tech Debt

| ID | Blocker | Note |
|----|---------|------|
| TD-01 | `sqlalchemy>=2.1` | Async session typing improvements — wait for SA 2.1 stable |
| TD-02 | `fastapi>=0.122` | `starlette==0.52.1` Dependabot PR-25 irresolvable until FastAPI bumps cap |
| TD-03 | `pydantic>=2.12` | Minor `model_validator` deprecation warnings — no functional impact |

---

## Cross-Cutting Health Checks (Cycle 8 close)

- ✅ `python_version` in mypy, ruff `target-version`, and CI `setup-python` all aligned to **3.11**
- ✅ Coverage gate consistent: `pyproject.toml` `fail_under`, `Makefile` `--cov-fail-under`, CI all **68**
- ✅ `pip-audit` version consistent: `requirements-dev.txt` and CI both **2.9.0**
- ✅ `lint` target uses `ruff check` (same tool as CI SAST ruff step)
- ✅ `typecheck` target covers `src/ api/ observability/ pipelines/`
- ✅ `make test-unit` runs by path, not by marker
- ✅ Non-root USER in Dockerfile; pip bumped to 25.1.1
- ✅ No hardcoded secrets anywhere in repo
- ✅ TruffleHog + Bandit + mypy all active in CI
- ✅ README coverage gates match enforced CI values (≥68% unit / ≥40% integration)
- ✅ Docker port references consistent: `8080` across Dockerfile, docker-compose.yml, healthchecks
- ✅ Prod compose port mapping corrected: `8000:8080` (host:container)
- ✅ Redis healthcheck authenticated in both dev and prod compose files
- ✅ OTEL collector ≥0.116 (CVE range resolved); Jaeger current stable
- ✅ RS256 primary algorithm clearly labelled in `.env.example`; HS256 marked as dev/CI fallback only
- ✅ `api/app.py` decomposed to 47-line factory shell (R-GOD complete)
- ✅ All 13 API routes registering correctly on import
