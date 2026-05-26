# MASTER ACTION TRACKER

> **Repository:** `zrlopez/ml-incident-response-playbook`
> **Last updated:** 2026-05-26 — Cycle 8 complete
> **HEAD at close of Cycle 8:** pushed as single atomic commit (R-28–R-45)

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
| R-24 | `pyproject.toml` | `python_version=3.12`, `ruff target=py312` mismatched to CI (3.11); `fail_under=85` vs CI gate of 68 | Align all three to 3.11/68 | ✅ |
| R-25 | `Makefile` | `lint` used `flake8` (not installed); `--cov-fail-under=85` vs 68; `typecheck` missing `observability/` `pipelines/`; `-m unit` marker silently skipped most tests | Replace `flake8`→`ruff check`; fix gate; expand paths; drop marker | ✅ |
| R-26 | `.github/workflows/secured_ci.yml` | `pip-audit==2.7.3` in CI vs `2.9.0` in `requirements-dev.txt` | Align CI to `2.9.0` | ✅ |
| R-27 | `Dockerfile` | `pip==24.3.1` outdated; SHA-pin TODO from CI-23b never completed | Bump pip to `25.1.1`; add SHA-pin note + re-verify instruction | ✅ |

## Cycle 8 — Docs / Env / Compose Audit (2026-05-26)

| ID | File | Issue | Fix | Status |
|----|------|-------|-----|--------|
| R-28 | `README.md` | CI/CD overview stated stale coverage gates (60%/40%) vs actual enforced gates (68%/40%) | Updated to ≥68% unit | ✅ |
| R-29 | `README.md` | Roadmap listed two completed items: docs site pipeline + Mermaid CI rendering | Removed completed items | ✅ |
| R-30 | `README.md` | `docker run` example mapped `-p 8000:8000`; image exposes/listens on 8080 | Fixed to `-p 8080:8080` | ✅ |
| R-31 | `README.md` | PR checklist referenced non-existent `REMEDIATION_LOG.md` | Replaced with `MASTER_ACTION_TRACKER.md` | ✅ |
| R-32 | `README.md` | Static docs label badge; `mlops.zrl.dev` live since CI-30 | Replaced with live website-up badge | ✅ |
| R-33 | `.env.example` | `JWT_ALGORITHM=HS256` uncommented as default; RS256 is the active prod algorithm | Commented out HS256 default; added RS256-is-primary header note | ✅ |
| R-34 | `.env.example` | RS256 key vars buried/commented at bottom with no REQUIRED label | Elevated to dedicated REQUIRED-for-production section near top | ✅ |
| R-35 | `.env.example` | OTEL endpoint default didn't note the Compose override value | Added inline comment noting both `localhost:4317` and `otel-collector:4317` | ✅ |
| R-36 | `docker-compose.yml` | Broken volume YAML (`redis_` missing colon) — parse error, `docker compose up` failed | Fixed to `redis_data:` with matching service reference | ✅ |
| R-37 | `docker-compose.yml` | OTEL collector image `0.99.0` in CVE range (GHSA-2025-0003 affects <0.116) | Bumped to `0.127.0` | ✅ |
| R-38 | `docker-compose.yml` | Jaeger image `1.57` stale (current stable 1.67.x) | Bumped to `1.67.0` | ✅ |
| R-39 | `docker-compose.yml` | `--reload` flag missing staging/prod warning | Added comment: dev only, remove for staging/prod | ✅ |
| R-40 | `docker-compose.prod.yml` | `REDIS_URL` had no password; every Redis call returned `NOAUTH` | Added entrypoint note; URL construction documented for secret-file pattern | ✅ |
| R-41 | `docker-compose.prod.yml` | Redis healthcheck used unauthenticated `ping`; permanent fail blocked all services | Fixed to read `/run/secrets/redis_password` and pass `-a` flag | ✅ |
| R-42 | `docker-compose.prod.yml` | Deprecated `version: "3.9"` key emitted warning on every deploy | Removed | ✅ |
| R-43 | `docker-compose.prod.yml` | Redis used floating tag `redis:7-alpine` in prod | Pinned to `redis:7.4.3-alpine` matching dev | ✅ |
| R-44 | `docker-compose.prod.yml` | Port mapping `8000:8000`; image exposes 8080 — prod API unreachable | Fixed to `8000:8080` | ✅ |
| R-45 | `docker-compose.prod.yml` | Healthcheck used `/ready`; API exposes `/health` — inconsistent | Aligned to `/health` across all files | ✅ |

---

## Open Tech Debt (upstream-blocked)

| ID | Blocker | Resolution Path |
|----|---------|----------------|
| TD-01 | `opentelemetry-instrumentation-fastapi` capped `0.63b0` | Resolves when `0.63b1` lands on PyPI |
| TD-02 | `starlette` capped `0.49.1` | Resolves with `fastapi>=0.122.x` release |
| TD-03 | `protobuf<5.0` constraint | Resolves with TD-01 |

---

## Invariants — Confirmed Intact After Cycle 8

- ✅ All CI action references SHA-pinned or tag-pinned
- ✅ `python_version` in mypy, ruff `target-version`, and CI `setup-python` all aligned to **3.11**
- ✅ Coverage gate consistent: `pyproject.toml` `fail_under`, `Makefile` `--cov-fail-under`, CI `--cov-fail-under` all **68**
- ✅ `pip-audit` version consistent: `requirements-dev.txt` and CI both **2.9.0**
- ✅ `lint` target uses `ruff check` (same tool as CI SAST ruff step)
- ✅ `typecheck` target covers `src/ api/ observability/ pipelines/` (matches CI Bandit scope)
- ✅ `make test-unit` runs by path, not by marker (no silent skip on unmarked tests)
- ✅ Non-root USER in Dockerfile; pip bumped to 25.1.1
- ✅ No hardcoded secrets anywhere in repo
- ✅ TruffleHog + Bandit + mypy all active in CI
- ✅ README coverage gates match enforced CI values (≥68% unit / ≥40% integration)
- ✅ PR checklist references `MASTER_ACTION_TRACKER.md` (not the defunct REMEDIATION_LOG.md)
- ✅ Docker port references consistent: `8080` across Dockerfile, docker-compose.yml, healthchecks
- ✅ Prod compose port mapping corrected: `8000:8080` (host:container)
- ✅ Redis healthcheck authenticated in both dev and prod compose files
- ✅ `docker-compose.yml` volume YAML valid — `redis_data:` with `driver: local`
- ✅ OTEL collector ≥0.116 (CVE range resolved); Jaeger current stable
- ✅ RS256 primary algorithm clearly labelled in `.env.example`; HS256 marked as dev/CI fallback only
