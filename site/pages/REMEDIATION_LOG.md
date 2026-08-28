# Remediation Log — ML Incident Response API

<!-- Last updated: 2026-05-29 | Cycle 3 COMPLETE (partial revert) -->

> Single source of truth for all remediation and roadmap work on the ML Incident Response Playbook.
> Status updated after every remediation cycle. Never remove completed items — mark VALIDATED.

---

## Status Legend

| Status | Meaning |
|--------|----------|
| BACKLOG | Identified, not yet started |
| IN PROGRESS | Actively being worked |
| BLOCKED | Waiting on dependency |
| FIXED | Code/config change committed to main |
| VALIDATED | Fix verified by test / CI pass |
| REVERTED | Committed then reverted; back to BACKLOG with note |
| DEFERRED | Intentionally postponed with documented rationale |

---

## Priority Tiers

| Tier | Criteria |
|------|----------|
| **CRITICAL** | Security vulnerability, broken runtime, CI hard failure |
| **HIGH** | Coverage regression, CI gate mismatch, missing safety net |
| **MEDIUM** | Architectural debt, observability gap, DX friction |
| **LOW** | Polish, documentation, portfolio signal |

---

## Active Tracker

### 🔴 CRITICAL / HIGH — Fix First

| ID | Phase | Category | Issue | Sev | Status | Blocking Deps | Files Affected | Validation |
|----|-------|----------|-------|-----|--------|---------------|----------------|------------|
| R-P1 | Cycle 2 | CI/CD | Integration coverage gate 53% (CI-66b); recovery to ≥65% deferred as CI-67 | HIGH | BACKLOG | CI-67 fixture work | `secured_ci.yml` | Gate restored ≥65%; CI green |
| R-P12 | Cycle 2 | Testing | CI-67 open: Redis / lifespan / auth paths unreachable in integration fixtures | HIGH | BACKLOG | CI-67 | `tests/integration/` | Integration coverage ≥65%; CI-67 closed |
| R-P23 | Phase 12 | Architecture | Refactor `src/incident_tracker.py` → thin facade over domain/services/repositories | HIGH | BACKLOG | R-P22 | `src/domain/`, `src/services/`, `src/repositories/` | All existing tests pass; mypy clean |
| R-P57 | Phase 18 | Code Hygiene | Strip all inline micro-changelogs from source files — history belongs in git and CHANGELOG.md only | HIGH | BACKLOG | — | All `src/`, `api/`, `tests/`, config files | `grep -r "# .*20[0-9][0-9]-" src/ api/` returns zero hits; CI green |

### 🟠 MEDIUM — Do Next

| ID | Phase | Category | Issue | Sev | Status | Blocking Deps | Files Affected | Validation |
|----|-------|----------|-------|-----|--------|---------------|----------------|------------|
| R-P19 | Phase 13 | Architecture | `src/incident_tracker.py` module-level `_engine` singleton (Phase 13 DI migration) | MEDIUM | DEFERRED | R-P23 | `src/incident_tracker.py` | Engine constructed only inside lifespan context |
| R-P24 | Phase 12 | Architecture | Collapse deps to single source of truth: `pyproject.toml` + pip-compile | MEDIUM | BACKLOG | R-P23 | `pyproject.toml`, `requirements.txt` | pip-compile round-trips cleanly; lockfile-check CI green |
| R-P25 | Phase 12 | Architecture | Add minimum credible content to `infrastructure/` — Terraform stub + README | MEDIUM | BACKLOG | — | `infrastructure/main.tf`, `infrastructure/README.md` | Files present; `terraform validate` passes |
| R-P26 | Phase 12 | Architecture | Add minimum credible content to `dbt/` — README + one model stub | MEDIUM | BACKLOG | — | `dbt/README.md`, `dbt/models/incidents.sql` | Files present; renders in docs |
| R-P27 | Phase 12 | Architecture | Add minimum credible content to `orchestration/` — README explaining DAG pattern | MEDIUM | BACKLOG | — | `orchestration/README.md` | File present; explains Prefect/Airflow integration |
| R-P28 | Phase 12 | Architecture | Update Architecture Mermaid diagram in README to reflect real code path | MEDIUM | BACKLOG | R-P23 | `README.md` | Diagram matches: FastAPI → Auth → Services → Domain → Postgres/Redis |
| R-P33 | Phase 13 | CI/CD | Fix README CI/CD section coverage discrepancy (`says ≥68%`, gate is `75%`) | MEDIUM | BACKLOG | — | `README.md` | README states correct gate value |
| R-P34 | Phase 14 | Runbooks | Expand all runbooks to operational template format | MEDIUM | BACKLOG | — | `runbooks/*.md` | All runbooks have metadata table, query examples, decision tree, escalation |
| R-P35 | Phase 14 | Runbooks | Add `runbooks/model_rollback.md` | MEDIUM | BACKLOG | R-P34 | `runbooks/model_rollback.md` | File present; meets operational template standard |
| R-P36 | Phase 14 | Runbooks | Add `runbooks/feature_store_corruption.md` | MEDIUM | BACKLOG | R-P34 | `runbooks/feature_store_corruption.md` | File present; meets operational template standard |
| R-P37 | Phase 14 | Runbooks | Add `runbooks/runbook_test_log.md` — game-day exercise evidence | MEDIUM | BACKLOG | R-P34 | `runbooks/runbook_test_log.md` | File present; at least one exercise entry documented |
| R-P38 | Phase 14 | Observability | Add `configs/slos.yml` — numeric SLO definitions | MEDIUM | BACKLOG | — | `configs/slos.yml` | File present; values match Grafana dashboard thresholds |
| R-P39 | Phase 15 | Observability | Add `api/metrics.py` — Prometheus endpoint with Counter, Histogram, Gauge | MEDIUM | BACKLOG | — | `api/metrics.py` | `/metrics` endpoint responds; curl shows metric names |
| R-P40 | Phase 15 | Observability | Register metrics router in `api/main.py` | MEDIUM | BACKLOG | R-P39 | `api/main.py` | `GET /metrics` returns 200 with Prometheus text format |
| R-P41 | Phase 15 | Observability | Instrument `create_incident` path with metric labels | MEDIUM | BACKLOG | R-P39, R-P40 | `api/routers/incidents.py` | Metrics visible in Prometheus scrape after POST |
| R-P42 | Phase 15 | Observability | Bind runbook threshold values to real Prometheus query expressions in `configs/slos.yml` | MEDIUM | BACKLOG | R-P38, R-P39 | `configs/slos.yml` | SLO file references real metric names from `api/metrics.py` |
| R-P43 | Phase 15 | Observability | Update Grafana dashboard JSON to use real metric names | MEDIUM | BACKLOG | R-P39, R-P42 | `dashboards/ml_operations_overview.json` | Dashboard panels show live data in local Compose stack |
| R-P44 | Phase 16 | MLOps | Confirm HF Space org/slug — README shows `zrlo/ml-incident-api`, verify correct | MEDIUM | BACKLOG | — | `README.md`, `deploy-hf.yml` | HF Space URL resolves; workflow targets correct slug |
| R-P45 | Phase 16 | MLOps | Verify `Dockerfile` port — HF `app_port: 8080`; confirm FastAPI binds `0.0.0.0:8080` | MEDIUM | BACKLOG | — | `Dockerfile` | Container starts and responds on 8080 |
| R-P46 | Phase 16 | MLOps | Provision external Postgres (Neon free tier) — connect string → HF Secret | MEDIUM | BACKLOG | — | HF Space Secrets | `GET /ready` returns 200 on live HF Space |
| R-P47 | Phase 16 | MLOps | Provision external Redis (Upstash free tier) — connect string → HF Secret | MEDIUM | BACKLOG | — | HF Space Secrets | Rate limiting functional on live HF Space |
| R-P48 | Phase 16 | MLOps | Add `scripts/seed_demo_user.py` — seeds read-only demo user on first boot | MEDIUM | BACKLOG | — | `scripts/seed_demo_user.py` | Script idempotent; demo user exists after run |
| R-P49 | Phase 16 | MLOps | Add `make deploy-hf` + `make hf-status` Makefile targets | MEDIUM | BACKLOG | R-P44, R-P45 | `Makefile` | `make deploy-hf` pushes to HF Space remote |
| R-P50 | Phase 16 | MLOps | Smoke-test live HF endpoint: `GET /health`, `POST /auth/token`, `GET /incidents` | MEDIUM | BACKLOG | R-P46, R-P47, R-P48 | — | All three requests return expected responses on live Space |

### 🟡 LOW — Polish & Portfolio

| ID | Phase | Category | Issue | Sev | Status | Blocking Deps | Files Affected | Validation |
|----|-------|----------|-------|-----|--------|---------------|----------------|------------|
| R-P51 | Phase 17 | Portfolio | Add "What Senior Reviewers Will Find" section to README | LOW | BACKLOG | R-P28, R-P33 | `README.md` | Section present; copy is accurate and current |
| R-P52 | Phase 17 | Portfolio | Add "Known Limitations" callout section to README | LOW | BACKLOG | — | `README.md` | Section present; no overselling |
| R-P53 | Phase 17 | Portfolio | Update Roadmap section to strategic format: Q3 2026 / Q4 2026 / Aspirational | LOW | BACKLOG | R-P50 | `README.md` | Roadmap reflects actual planned phases |
| R-P54 | Phase 17 | Portfolio | Verify all badge URLs resolve and are accurate | LOW | BACKLOG | R-P20 | `README.md` | All badges return 200; values match CI state |
| R-P55 | Phase 17 | Portfolio | Verify all internal doc links are not broken | LOW | BACKLOG | R-P34–R-P37 | `README.md`, `docs/` | `mkdocs build --strict` passes with 0 broken links |
| R-P56 | Phase 17 | Portfolio | Final README read-through — remove stale Fly.io references; verify `zrl.dev` link | LOW | BACKLOG | R-P51–R-P55 | `README.md` | Zero Fly.io references; `zrl.dev` links resolve |

---

## Completed Archive

### Cycle 3 (2026-05-29)

| ID | Issue | Commit | Resolution |
|----|-------|--------|------------|
| R-P22 | Characterization tests for `src/incident_tracker.py` | `1db8d11` | `tests/unit/test_incident_tracker_char.py` added; full CRUD, keyset pagination, state machine, init_db, get_session coverage |
| R-P4 | Semgrep fails for forks/Dependabot with empty token | `1db8d11` | `if: env.SEMGREP_APP_TOKEN != ''` condition added; hard gate retained for owned branches |
| R-P7 | `CONTRIBUTING.md` absent | `1db8d11` | Full onboarding guide added |
| R-P13 | `docker-compose.yml` missing/unverified | `1db8d11` | Verified and hardened: postgres:16-alpine + redis:7-alpine with health-checks |
| R-P20 | README badges absent | `1db8d11` | Python version and security badges added; CI/codecov/Codacy confirmed present |
| R-P16 | `/healthz`/`/readyz` K8s probe rename | `1db8d11` → REVERTED `098fe0b` | Reverted 2026-05-29 — no Kubernetes in project; broke 5 unit tests expecting `/health` and `/ready`. Routes restored to originals. |

### Cycle 2 (2026-05-29)

| ID | Issue | Commit | Resolution |
|----|-------|--------|------------|
| R-P11 | SlowAPI `get_remote_address` stored raw IP as rate-limiter Redis key — HIGH-01 final PII vector | `660005862` | Replaced with `_rate_limit_key()`: SHA-256(best-available-identifier)[:16]; raw IPs no longer enter limiter state |
| R-P21 | No regression test for HIGH-01 privacy protections across middleware + rate limiter | `660005862` | Added `tests/unit/test_middleware_pii.py` — 5 tests |

### Cycle 1 (2026-05-29)

| ID | Issue | Commit | Resolution |
|----|-------|--------|------------|
| R-P2 | Duplicate `lint:` target in Makefile — mypy silently skipped on `pipelines/` | `0890c89` | Single deduplicated target; `pipelines/` added to mypy + ruff scope |
| R-P3 | `test-int` gate 65% local vs 53% CI | `0890c89` | Aligned to 53%; CI-67 recovery path documented |
| R-P5 | Pre-commit mypy `--ignore-missing-imports` diverges from CI strict config | `b028d6c` | Removed flag; added stub deps to `additional_dependencies` |
| R-P6 | `MASTER_ACTION_TRACKER.md` reference in CHANGELOG | `171759d` | File merged into this log (2026-05-29); local copy deleted |
| R-P8 | `CODEOWNERS` minimal | `af04cc5` | Hardened with explicit security-sensitive and dependency manifest paths |
| R-P9 | `RequestTimeoutMiddleware` logs raw client IP | `c7ea849` | `_pseudo_ip()` applied |
| R-P10 | `MaxBodySizeMiddleware` logs raw client IP in 2 branches | `c7ea849` | `_pseudo_ip()` applied to both branches |
| R-P29 | `pytest-xdist` in `requirements-dev.txt` | pre-existing | Confirmed already present; `-n auto` wired in CI |
| R-P30 | `test_model_registry_thread_safety.py` | pre-existing | Confirmed already present in CI test list |
| R-P31 | Redis denylist concurrency tests | pre-existing | Confirmed `test_redis_denylist_concurrency.py` already present |
| R-P32 | `test_incident_service_contract.py` | pre-existing | Confirmed already present in CI test list |

---

## Pre-Engagement Archive

### Phase 6 — Repo Hygiene + CI Accuracy (2026-05-27)

| ID | Finding | Sev | Status | Files Changed |
|----|---------|-----|--------|---------------|
| R-02 | Orphaned `.github/release-placeholder-v110.txt` removed | LOW | ✅ CLOSED | `.github/release-placeholder-v110.txt` |
| R-06 | Fabricated Docker digest claim removed; honest TODO added pending network verification | HIGH | ✅ CLOSED | `Dockerfile` |
| R-08 | `secured_ci.yml` SHA reference block stale for `setup-python` | HIGH | ✅ CLOSED | `.github/workflows/secured_ci.yml` |
| R-09 | `secured_ci.yml` SHA reference block stale for `upload-artifact` | HIGH | ✅ CLOSED | `.github/workflows/secured_ci.yml` |
| R-10 | Workflow permissions audit completed for `secured_ci.yml`, `mermaid-render.yml`, `stale.yml`, `codeql.yml` | MED | ✅ PARTIAL | `.github/workflows/*.yml` |
| R-25 | `mermaid-render.yml` SHA reference block stale for `setup-node`; loop safety validated | LOW | ✅ CLOSED | `.github/workflows/mermaid-render.yml` |
| CI-51 | `stale.yml` floating tag `actions/stale@v9` SHA-pinned to verified commit | MED | ✅ CLOSED | `.github/workflows/stale.yml` |
| CI-52 | `docs.yml` SHA reference block stale for `setup-python`; synced to live pin | LOW | ✅ CLOSED | `.github/workflows/docs.yml` |

### Phase 3 — Architecture (Complete)

| ID | Finding | Sev | Status | Files Changed |
|----|---------|-----|--------|---------------|
| ARCH-01 | HS256 symmetric JWT — upgrade to RS256 + JWKS rotation | HIGH | ✅ CLOSED | `src/auth/jwt_rs256.py`; JWKS endpoint `/.well-known/jwks.json` |
| ARCH-02 | `passlib` → `argon2-cffi` password hashing (OWASP 2024) | HIGH | ✅ CLOSED | `src/auth/password.py`, `requirements.txt` |
| ARCH-03 | `_USERS` dict → `PostgresUserRepository` database-backed | HIGH | ✅ CLOSED | `src/users/repository.py`, `api/app.py`, `alembic/versions/0001_initial_schema.py` |
| ARCH-04 | Secrets via Vault / AWS Secrets Manager (zero-secret images) | HIGH | ✅ CLOSED | `docs/policies/secrets_management.md` |
| ARCH-05 | GDPR `/users/me/export` and `/users/me` DELETE endpoints | MED | ✅ CLOSED | `api/gdpr_routes.py` |
| ARCH-06 | Argon2 rehash-on-login migration (zero-downtime) | MED | ✅ CLOSED | `src/auth/password.py`, `src/users/repository.py` |
| ARCH-07 | Rate limiting: per-user sliding window via Redis | MED | ✅ CLOSED | `api/rate_limit.py` |

### Phase 2 — Infrastructure, Config, CI/CD (Complete)

| ID | Finding | Sev | Status | Files Changed |
|----|---------|-----|--------|---------------|
| CI-06 | `pip-audit` had `‖ true` bypass — CVE findings silently ignored | HIGH | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-07 | No semgrep SAST — OWASP Top 10 pattern coverage gap | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-08 | GitHub Actions not pinned to SHA digest — tag-mutation risk | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-09 | Coverage threshold 70% — insufficient for security-critical auth paths | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` (raised to 75%) |
| CI-11 | No dependency-review action on PRs | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-12 | Container scan built with ENVIRONMENT=test (misses production-mode issues) | LOW | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-13 | REDIS_PASSWORD not in CI test environment | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` |
| CI-14 | GitHub Actions workflow-level `write-all` permissions | MED | ✅ CLOSED | `ci_cd/secure-ci.yml` (per-job least-privilege) |
| CFG-01 | No centralized Settings class — os.environ scattered across modules | MED | ✅ CLOSED | `src/config.py` (pydantic-settings + startup validation) |
| CFG-02 | `.gitignore` had unresolved merge conflict markers | HIGH | ✅ CLOSED | `.gitignore` |
| CFG-03 | `.env.example` missing REDIS_PASSWORD and DEV_*_PASSWORD | MED | ✅ CLOSED | `.env.example` |

### Phase 1 — Critical Security Hardening (Complete)

| ID | Finding | Sev | Status | Files Changed |
|----|---------|-----|--------|---------------|
| P1-01 | `noreply@` security contact replaced with GitHub Private Vulnerability Reporting | HIGH | ✅ CLOSED | `SECURITY.md` |
| P1-02 | `SECURITY.md` controls table corrected to match actual repository state | MED | ✅ CLOSED | `SECURITY.md` |
| P1-03 | Airflow scope item removed — template copy-paste artifact, project has no Airflow | LOW | ✅ CLOSED | `SECURITY.md` |
| P1-04 | All GitHub Actions workflows pinned to SHA digests (supply chain hardening) | HIGH | ✅ CLOSED | `.github/workflows/ci.yml` |
| P1-05 | Trivy gate restored to blocking `exit-code: '1'` (was bypassed as CI-26) | HIGH | ✅ CLOSED | `.github/workflows/ci.yml` |
| P1-06 | `pip-audit` JSON artifact generation decoupled from hard gate (removes silent failure) | MED | ✅ CLOSED | `.github/workflows/ci.yml` |
| P1-07 | CI secret availability guard added to integration-tests job | MED | ✅ CLOSED | `.github/workflows/ci.yml` |
| P1-08 | CodeQL semantic SAST workflow added (`security-and-quality` query suite) | HIGH | ✅ CLOSED | `.github/workflows/codeql.yml` |
| CI-10 | Branch protection ruleset enforced on `main` | MED | ✅ CLOSED | GitHub repo Settings — Rulesets |

### Phase 12

### ML-09 — Integration coverage gate raised 53% → 65% (CI-67 Complete)

**Date:** 2026-08-05  
**Files:** `tests/integration/test_inference_integration.py` (new), `.github/workflows/secured_ci.yml`, `Makefile`, `README.md`

**Problem:** Integration `--cov-fail-under` gate was pinned at 53% (CI-66b deferral), while README claimed ≥65%. The inference router (`api/routers/inference.py`) had zero integration-level HTTP coverage. The mismatch was a direct factual contradiction visible in CI.

**Fix:** Added `tests/integration/test_inference_integration.py` with 10 ASGI-transport tests covering:
- IT-INF-01..02: Happy path 200 (nominal + extreme vector)
- IT-INF-03..04: 503 paths (missing artifact, predict() raises)
- IT-INF-05: 401/403 unauthenticated gate
- IT-INF-06: 422 Pydantic validation rejection
- IT-INF-07..08: GET /anomaly/health (threshold exposed, artifact_exists reflected)
- IT-INF-09: model_version constant round-trip
- IT-INF-10: inference_latency_ms non-negative invariant

No Postgres or Redis required — ModelRegistry is patched per-test via `unittest.mock.patch`. Gate raised to 65% in both `secured_ci.yml` and `Makefile`. README Scope section updated to match.

**Gate:** `--cov-fail-under=65` ✅

---

 — ML Layer + Observability Hardening (2026-08-05)

| ID | Finding | Sev | Status | Files Changed |
|----|---------|-----|--------|---------------|
| ML-01 | `_ANOMALY_THRESHOLD` was a hard-coded `0.0` constant; no env-var override; registry logs used stdlib `logging` not structlog | HIGH | ✅ CLOSED | `ml_models/incident_anomaly/registry.py` |
| ML-02 | `scripts/train_model.py` did not auto-generate SHA-256 artifact checksum; `model_metadata.json` missing `artifact_sha256` and `anomaly_threshold` fields | MED | ✅ CLOSED | `scripts/train_model.py` |
| ML-03 | `api/routers/inference.py` used stdlib `logging.getLogger(__name__)` — inference logs bypassed structlog PII-scrubbing and JSON pipeline | HIGH | ✅ CLOSED | `api/routers/inference.py` |
| ML-04 | `MODEL_CARD.md` Output Schema implied threshold was fixed; no threshold configuration guidance; `confidence` not annotated as uncalibrated | MED | ✅ CLOSED | `MODEL_CARD.md` |
| ML-05 | No ADR covering anomaly model design decisions (algorithm selection, contamination rationale, threshold strategy, drift wiring) | MED | ✅ CLOSED | `docs/adr/ADR-010-anomaly-model-design.md` (new) |
| ML-06 | `MODEL_CARD.md` missing Productionization Gaps section; honest gap analysis not documented | LOW | ✅ CLOSED | `MODEL_CARD.md` |
| ML-07 | `runbooks/model_degradation.md` referenced drift detection generically without tying to implemented `check_drift_suite()`, `ANOMALY_THRESHOLD` env var, or API health endpoint | LOW | ✅ CLOSED | `runbooks/model_degradation.md` |
| ML-08 | `Dockerfile.dev` base image `python:3.11-slim` not digest-pinned; no supply-chain rationale for intentional omission | LOW | ✅ CLOSED | `Dockerfile.dev` |

---

### Phase 0 — Critical/High/Medium (Complete)

| ID | Finding | Sev | Status | Files Changed |
|----|---------|-----|--------|---------------|
| CRIT-A | Hard-coded stub user passwords `"admin-dev-only"` silently used as fallback | CRIT | ✅ CLOSED | `api/app.py` |
| CRIT-B | Async/sync denylist boundary: `asyncio.get_event_loop().run_until_complete()` inside async context raised `RuntimeError` on every revocation | CRIT | ✅ CLOSED | `src/redis_denylist.py`, `api/app.py` ×3 |
| CRIT-C | Airflow + dbt dependencies in API image: +800 MB, arbitrary code exec surface | CRIT | ✅ CLOSED | `requirements.txt`, new `requirements-airflow.txt` |
| HIGH-A | Redis: no AUTH, all-interface bind (`0.0.0.0`), no password | HIGH | ✅ CLOSED | `docker-compose.yml`, `.env.example` |
| HIGH-B | `python-jose` CVE-2024-33663 (JWT algorithm confusion) | HIGH | ✅ CLOSED | `requirements.txt` (PyJWT), `api/app.py` |
| HIGH-C | No request body size limit — OOM DoS; no request timeout — slow-loris | HIGH | ✅ CLOSED | `api/middleware.py`, `api/app.py` |
| HIGH-D | `lru_cache` on `get_settings()` causes env var bleed between tests | HIGH | ✅ CLOSED | `tests/conftest.py`, `src/config.py` |
| MED-A | Dockerfile base image on floating tag (tag-mutation supply chain attack) | MED | ✅ CLOSED | `Dockerfile` (SHA-256 digest pinned on both stages) |
| MED-B | Full repo `.:/app` bind mount; `.dockerignore` missing | MED | ✅ CLOSED | `docker-compose.yml`, `.dockerignore` |
| MED-C | `SlowAPI` rate limiting missing on `/auth/token` | MED | ✅ CLOSED | `api/app.py` |
| MED-D | `passlib` unmaintained; `bcrypt` unpinned | MED | ✅ CLOSED | `requirements.txt` |
| MED-E | Missing OWASP security headers (CSP, HSTS, X-Frame-Options, etc.) | MED | ✅ CLOSED | `api/middleware.py` |
| LOW-A | `.DS_Store` tracked in git; merge conflicts in `.gitignore` | LOW | ✅ CLOSED | `.gitignore` |
| LOW-B | Telemetry ports bound to `0.0.0.0` (Jaeger UI, OTel receiver, Prometheus) | LOW | ✅ CLOSED | `docker-compose.yml` (loopback-only: `127.0.0.1:*`) |

### Pre-Engagement Milestones

| ID | Phase | Issue | Resolution | Date |
|----|-------|-------|------------|------|
| — | Phase 11 | Supply chain: `lockfile-check` CI job; pip-audit pre-commit; `make deps-compile` (CI-55) | Complete | 2026-05-28 |
| — | Phase 8 | Portfolio presentation: "Quick Proof of Quality" table in README | Partial complete | 2026-05-27 |
| — | Phase 7 | HF Spaces scaffolding: README YAML frontmatter; `deploy-hf.yml` workflow | Partial complete | 2026-05-27 |
| — | Phase 4 | Grafana + Prometheus infra: `docker-compose.yml`; `dashboards/ml_operations_overview.json` | Complete | 2026-05-27 |
| — | Phase 1 | Security hardening: Semgrep hard gate, `CI_POSTGRES_PASSWORD` secret ref, Bandit gate, per-job `permissions:` | Complete (CI-52) | 2026-05-26 |
| R-GOD | — | God-file `api/app.py` (1005 lines) extracted into routers, middleware, auth, config modules | Complete | Pre-engagement |

---

## CI-10 Ruleset Detail — `main` branch (enforced 2026-05-24)

| Rule | Setting |
|------|---------|
| Restrict deletions | ✅ Enabled |
| Require signed commits | ✅ Enabled |
| Require a PR before merging | ✅ Enabled |
| Required status checks | `secrets-scan`, `dependency-audit`, `SAST - Bandit + mypy`, `test`, `🧪 Tests (Python 3.11)` |
| Require branches to be up to date | ✅ Enabled |
| Block force pushes | ✅ Enabled |
