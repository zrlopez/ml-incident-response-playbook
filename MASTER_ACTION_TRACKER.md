# Master Action Tracker
<!-- Last updated: 2026-05-29 | Cycle 2 COMPLETE -->

> **Single source of truth** for all remediation and roadmap work on the ML Incident Response Playbook.
> `MASTER_CHECKLIST.md` has been deleted and fully absorbed here.
> Status updated after every remediation cycle. **Never remove completed items — mark VALIDATED.**

## Status Legend

| Status | Meaning |
|--------|---------|
| BACKLOG | Identified, not yet started |
| IN PROGRESS | Actively being worked |
| BLOCKED | Waiting on dependency |
| FIXED | Code/config change committed to main |
| VALIDATED | Fix verified by test / CI pass |
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

| ID | Phase | Category | Issue | Severity | Status | Blocking Deps | Files Affected | Validation |
|----|-------|----------|-------|----------|--------|---------------|----------------|------------|
| R-P1 | Cycle 2 | CI/CD | Integration coverage gate 53% (CI-66b); recovery to ≥65% deferred as CI-67 | HIGH | BACKLOG | CI-67 fixture work | `secured_ci.yml` | Gate restored ≥65%; CI green |
| R-P12 | Cycle 2 | Testing | CI-67 open: Redis / lifespan / auth paths unreachable in integration fixtures | HIGH | BACKLOG | CI-67 | `tests/integration/` | Integration coverage ≥65%; CI-67 closed |
| R-P4 | Cycle 2 | SAST CI | Semgrep step fails for forks/Dependabot with empty `SEMGREP_APP_TOKEN` | MEDIUM | BACKLOG | — | `.github/workflows/secured_ci.yml` | Fork PRs pass; hard gate retained for owned PRs |
| R-P22 | Phase 12 | Architecture | Write characterization tests for `src/incident_tracker.py` **before** refactor (safety net) | HIGH | BACKLOG | — | `tests/unit/test_incident_tracker_char.py` | Tests pass; coverage ≥80% on that module |
| R-P23 | Phase 12 | Architecture | Refactor `src/incident_tracker.py` → thin facade over `src/domain/`, `src/services/`, `src/repositories/` | HIGH | BACKLOG | R-P22 | `src/domain/`, `src/services/`, `src/repositories/` | All existing tests pass; mypy clean |
| R-P19 | Phase 13 | Architecture | `src/incident_tracker.py` module-level `_engine` singleton (Phase 13 DI migration) | MEDIUM | DEFERRED | R-P23 | `src/incident_tracker.py` | Engine constructed only inside lifespan context |

### 🟠 MEDIUM — Do Next

| ID | Phase | Category | Issue | Severity | Status | Blocking Deps | Files Affected | Validation |
|----|-------|----------|-------|----------|--------|---------------|----------------|------------|
| R-P7 | Cycle 2 | Repo Hygiene | `CONTRIBUTING.md` absent — no onboarding path | MEDIUM | BACKLOG | — | `CONTRIBUTING.md` | File present; covers setup, branching, PR, commit conventions |
| R-P13 | Cycle 2 | MLOps | `docker-compose.yml` referenced in `make ci-local` — verify / create | MEDIUM | BACKLOG | — | `docker-compose.yml` | `docker-compose up -d postgres redis` succeeds on clean clone |
| R-P16 | Cycle 2 | Observability | No `healthz`/`readyz` endpoint separation for K8s liveness/readiness probes | MEDIUM | BACKLOG | — | `api/routers/health.py` | Both endpoints respond correctly; unit tested |
| R-P24 | Phase 12 | Architecture | Collapse deps to single source of truth: `pyproject.toml` + `pip-compile` generated `requirements.txt` | MEDIUM | BACKLOG | R-P23 | `pyproject.toml`, `requirements.txt` | `pip-compile` round-trips cleanly; lockfile-check CI job green |
| R-P25 | Phase 12 | Architecture | Add minimum credible content to `infrastructure/` — Terraform stub (`main.tf`) + `README.md` | MEDIUM | BACKLOG | — | `infrastructure/main.tf`, `infrastructure/README.md` | Files present; `terraform validate` passes |
| R-P26 | Phase 12 | Architecture | Add minimum credible content to `dbt/` — `README.md` + one model stub (`models/incidents.sql`) | MEDIUM | BACKLOG | — | `dbt/README.md`, `dbt/models/incidents.sql` | Files present; renders in docs |
| R-P27 | Phase 12 | Architecture | Add minimum credible content to `orchestration/` — `README.md` explaining DAG trigger pattern | MEDIUM | BACKLOG | — | `orchestration/README.md` | File present; explains Prefect/Airflow integration pattern |
| R-P28 | Phase 12 | Architecture | Update Architecture Mermaid diagram in `README.md` to reflect real code path | MEDIUM | BACKLOG | R-P23 | `README.md` | Diagram matches: `FastAPI → Auth → Services → Domain → Postgres/Redis` |
| R-P29 | Phase 13 | Testing | Add `pytest-xdist` to `requirements-dev.txt` (parallel test execution) | MEDIUM | BACKLOG | — | `requirements-dev.txt` | `-n auto` works in CI and locally |
| R-P30 | Phase 13 | Testing | Add `tests/unit/test_model_registry_thread_safety.py` (50 concurrent workers) | MEDIUM | BACKLOG | R-P29 | `tests/unit/test_model_registry_thread_safety.py` | Test passes under `-n auto`; no data race |
| R-P31 | Phase 13 | Testing | Add Redis denylist concurrency + expiry edge-case tests | MEDIUM | BACKLOG | R-P12 | `tests/unit/test_redis_denylist.py` | Tests pass; edge cases covered |
| R-P32 | Phase 13 | Testing | Add `tests/unit/test_incident_service_contract.py` (API↔service interface contract) | MEDIUM | BACKLOG | R-P23 | `tests/unit/test_incident_service_contract.py` | Tests pass; contract verified |
| R-P33 | Phase 13 | CI/CD | Fix README CI/CD section coverage discrepancy (`says ≥68%`, gate is `75%`) | MEDIUM | BACKLOG | — | `README.md` | README states correct gate value |
| R-P34 | Phase 14 | Runbooks | Expand all runbooks to operational template format (metadata, Prometheus queries, Mermaid decision tree, escalation) | MEDIUM | BACKLOG | — | `runbooks/*.md` | All runbooks have metadata table, query examples, decision tree, escalation |
| R-P35 | Phase 14 | Runbooks | Add `runbooks/model_rollback.md` | MEDIUM | BACKLOG | R-P34 | `runbooks/model_rollback.md` | File present; meets operational template standard |
| R-P36 | Phase 14 | Runbooks | Add `runbooks/feature_store_corruption.md` | MEDIUM | BACKLOG | R-P34 | `runbooks/feature_store_corruption.md` | File present; meets operational template standard |
| R-P37 | Phase 14 | Runbooks | Add `runbooks/runbook_test_log.md` — game-day exercise evidence | MEDIUM | BACKLOG | R-P34 | `runbooks/runbook_test_log.md` | File present; at least one exercise entry documented |
| R-P38 | Phase 14 | Observability | Add `configs/slos.yml` — numeric SLO definitions (error rate, latency p99, MTTR) | MEDIUM | BACKLOG | — | `configs/slos.yml` | File present; values match Grafana dashboard thresholds |
| R-P39 | Phase 15 | Observability | Add `api/metrics.py` — Prometheus endpoint with `Counter`, `Histogram`, `Gauge` definitions | MEDIUM | BACKLOG | — | `api/metrics.py` | `/metrics` endpoint responds; `curl` shows metric names |
| R-P40 | Phase 15 | Observability | Register metrics router in `api/main.py` | MEDIUM | BACKLOG | R-P39 | `api/main.py` | `GET /metrics` returns 200 with Prometheus text format |
| R-P41 | Phase 15 | Observability | Instrument `create_incident` path with metric labels (severity, status, latency) | MEDIUM | BACKLOG | R-P39, R-P40 | `api/routers/incidents.py` | Metrics visible in Prometheus scrape after POST |
| R-P42 | Phase 15 | Observability | Bind runbook threshold values to real Prometheus query expressions in `configs/slos.yml` | MEDIUM | BACKLOG | R-P38, R-P39 | `configs/slos.yml` | SLO file references real metric names from `api/metrics.py` |
| R-P43 | Phase 15 | Observability | Update Grafana dashboard JSON to use real metric names from `api/metrics.py` | MEDIUM | BACKLOG | R-P39, R-P42 | `dashboards/ml_operations_overview.json` | Dashboard panels show live data in local Compose stack |
| R-P44 | Phase 16 | MLOps | Confirm HF Space org/slug — README shows `zrlo/ml-incident-api`, verify correct | MEDIUM | BACKLOG | — | `README.md`, `deploy-hf.yml` | HF Space URL resolves; workflow targets correct slug |
| R-P45 | Phase 16 | MLOps | Verify `Dockerfile` port — HF `app_port: 8080`; confirm FastAPI binds `0.0.0.0:8080` | MEDIUM | BACKLOG | — | `Dockerfile` | Container starts and responds on 8080 |
| R-P46 | Phase 16 | MLOps | Provision external Postgres (Neon free tier) — connect string → HF Secret `DATABASE_URL` | MEDIUM | BACKLOG | — | HF Space Secrets | `GET /readyz` returns 200 on live HF Space |
| R-P47 | Phase 16 | MLOps | Provision external Redis (Upstash free tier) — connect string → HF Secret `REDIS_URL` | MEDIUM | BACKLOG | — | HF Space Secrets | Rate limiting functional on live HF Space |
| R-P48 | Phase 16 | MLOps | Add `scripts/seed_demo_user.py` — seeds read-only demo user on first boot | MEDIUM | BACKLOG | R-P16 | `scripts/seed_demo_user.py` | Script idempotent; demo user exists after run |
| R-P49 | Phase 16 | MLOps | Add `make deploy-hf` + `make hf-status` Makefile targets | MEDIUM | BACKLOG | R-P44, R-P45 | `Makefile` | `make deploy-hf` pushes to HF Space remote; `make hf-status` tails build logs |
| R-P50 | Phase 16 | MLOps | Smoke-test live HF endpoint: `GET /health`, `POST /auth/token`, `GET /incidents` | MEDIUM | BACKLOG | R-P46, R-P47, R-P48 | — | All three return expected responses on live URL |

### 🟡 LOW — Polish & Portfolio

| ID | Phase | Category | Issue | Severity | Status | Blocking Deps | Files Affected | Validation |
|----|-------|----------|-------|----------|--------|---------------|----------------|------------|
| R-P15 | Cycle 2 | DX | No `scripts/bootstrap.sh` for new developer onboarding | LOW | BACKLOG | R-P7 | `scripts/bootstrap.sh` | Script runs end-to-end on clean clone |
| R-P17 | Cycle 2 | Security | `.trivyignore` CVE-2026-0994 suppression needs GitHub Issue for review-date enforcement | LOW | BACKLOG | — | `.trivyignore`, GitHub Issues | Tracking issue created; review date 2026-08-24 |
| R-P18 | Cycle 2 | CI/CD | `dependency-review` job runs only on PRs — push to `main` has no dependency gate | LOW | BACKLOG | — | `secured_ci.yml` | Gap documented with rationale (GitHub Actions API limitation) |
| R-P20 | Cycle 2 | Repo Hygiene | No CI status badges in `README.md` — portfolio signal buried | LOW | BACKLOG | — | `README.md` | Badges visible at top; CI/coverage/docs all present |
| R-P14 | Cycle 2 | Repo Hygiene | CI index table in CHANGELOG stops at CI-53; CI-54–CI-66b undocumented | LOW | BACKLOG | — | `CHANGELOG.md` | Index covers through CI-66b |
| R-P51 | Phase 17 | Portfolio | Add "What Senior Reviewers Will Find" section to README (above Feature Highlights) | LOW | BACKLOG | R-P28, R-P33 | `README.md` | Section present; copy is accurate and current |
| R-P52 | Phase 17 | Portfolio | Add "Known Limitations" callout section to README (honest, professional) | LOW | BACKLOG | — | `README.md` | Section present; no overselling |
| R-P53 | Phase 17 | Portfolio | Update Roadmap section to strategic format: Q3 2026 / Q4 2026 / Aspirational | LOW | BACKLOG | R-P50 | `README.md` | Roadmap reflects actual planned phases |
| R-P54 | Phase 17 | Portfolio | Verify all badge URLs resolve and are accurate | LOW | BACKLOG | R-P20 | `README.md` | All badges return 200; values match CI state |
| R-P55 | Phase 17 | Portfolio | Verify all internal doc links (`docs/`, `runbooks/`, ADRs) are not broken | LOW | BACKLOG | R-P34–R-P37 | `README.md`, `docs/` | `mkdocs build --strict` passes with 0 broken links |
| R-P56 | Phase 17 | Portfolio | Final README read-through — remove stale Fly.io references; cross-check `zrl.dev` portfolio link | LOW | BACKLOG | R-P51–R-P55 | `README.md` | Zero Fly.io references; `zrl.dev` links resolve to correct repo |

---

## Completed Archive

### Cycle 2 (2026-05-29)

| ID | Issue | Commit | Resolution |
|----|-------|--------|------------|
| R-P11 | SlowAPI `get_remote_address` stored raw IP as rate-limiter Redis key — HIGH-01 final PII vector | this commit | Replaced with `_rate_limit_key()`: SHA-256(best-available-identifier)[:16]; `get_remote_address` removed from `api/config.py`; raw IPs no longer enter limiter state |
| R-P21 | No regression test for HIGH-01 privacy protections across middleware + rate limiter | this commit | Added `tests/unit/test_middleware_pii.py` — 5 tests covering `_pseudo_ip()` determinism, non-raw output, `_rate_limit_key()` hashing, X-Forwarded-For fallback, and static source guard |

### Cycle 1 (2026-05-29)

| ID | Issue | Commit | Resolution |
|----|-------|--------|------------|
| R-P2 | Duplicate `lint:` target in Makefile — mypy silently skipped on `pipelines/` | `0890c89` | Single deduplicated target; `pipelines/` added to mypy + ruff scope |
| R-P3 | `test-int` gate 65% local vs 53% CI — misleading dev experience | `0890c89` | Aligned to 53%; comment documents CI-67 recovery path |
| R-P5 | Pre-commit mypy `--ignore-missing-imports` diverges from CI strict config | `b028d6c` | Removed flag; added stub deps to `additional_dependencies` |
| R-P6 | `MASTER_ACTION_TRACKER.md` missing (CHANGELOG R-09 broken ref) | `171759d` | File created; full tracker state as of Cycle 1 |
| R-P8 | `CODEOWNERS` minimal — no explicit security or supply-chain path rules | `af04cc5` | Hardened with explicit security-sensitive and dependency manifest paths |
| R-P9 | `RequestTimeoutMiddleware` logs raw client IP (HIGH-01 regression) | `c7ea849` | `_pseudo_ip()` applied; 0 raw IP log refs remain |
| R-P10 | `MaxBodySizeMiddleware` logs raw client IP in 2 branches (HIGH-01 regression) | `c7ea849` | `_pseudo_ip()` applied to both branches |

### Pre-Engagement Archive (Before Cycle 1)

| ID | Phase | Issue | Resolution | Date |
|----|-------|-------|------------|------|
| Phase 1 | 1 | Security hardening: Semgrep hard gate, `CI_POSTGRES_PASSWORD` secret ref, Bandit gate, per-job `permissions:`, permission audit comment block | All complete (CI-52) | 2026-05-26 |
| Phase 4 | 4 | Grafana + Prometheus infra: `prometheus` + `grafana` services in `docker-compose.yml`; `dashboards/ml_operations_overview.json`; `observability/prometheus.yml` | Complete | 2026-05-27 |
| Phase 7 | 7 | HF Spaces scaffolding: `README.md` YAML frontmatter (`sdk: docker`, `app_port: 8080`); `deploy-hf.yml` workflow exists | Partial complete | 2026-05-27 |
| Phase 8 | 8 | Portfolio presentation: "Quick Proof of Quality" table in README; live HF Space URL row; `mlops.zrl.dev` docs row | Partial complete | 2026-05-27 |
| Phase 11 | 11 | Supply chain: `lockfile-check` CI job; `CODEOWNERS` verified; `docs/branch-protection-policy.md`; `pip-audit` pre-commit hook; `make deps-compile` target (CI-55) | Complete | 2026-05-28 |
| R-GOD | — | God-file `api/app.py` (1005 lines) | 10-step extraction into config, auth, schemas, middleware, routers, lifespan | 2026-05-26 |
| HIGH-01 (partial) | — | Raw IP in `trace_and_security_headers` | `_pseudo_ip()` SHA-256 pseudonymisation | 2026-05-27 |
| SEC-01 | — | JWT secret in plain env string | `SecretStr` wrap + `get_jwt_secret()` single accessor | 2026-05-26 |
| ARCH-07 | — | Stub users importable in production | `_STUB_ALLOWED_ENVIRONMENTS` guard + `RuntimeError` | 2026-05-26 |
| CI-55 | — | No lockfile drift detection | `lockfile-check` CI job + `make deps-compile` + pre-commit pip-audit hook | 2026-05-28 |

---

## Reprioritised Cycle Queue

### Cycle 2 Remaining Items — Security + DX
*R-P11 and R-P21 closed. Continue with remaining Cycle 2 backlog.*

| Priority | ID | Severity | Action |
|----------|----|----------|--------|
| 1 | R-P22 | HIGH | Write characterization tests for `src/incident_tracker.py` before any refactor |
| 2 | R-P4 | MEDIUM | Guard Semgrep step against fork empty-token failures |
| 3 | R-P7 | MEDIUM | Write `CONTRIBUTING.md` (setup, branching, commits, PR process) |
| 4 | R-P13 | MEDIUM | Verify/create `docker-compose.yml` (postgres 16, redis 7) |
| 5 | R-P16 | MEDIUM | Add `/healthz` + `/readyz` router with DB + Redis readiness checks |
| 6 | R-P20 | LOW | Add CI/coverage/docs badges to README header |

### Cycle 3 — Architecture + Coverage Recovery
*Refactor incident_tracker; raise integration coverage back to ≥65%.*

| Priority | ID | Action |
|----------|----|--------|
| 1 | R-P23 | Refactor `src/incident_tracker.py` to clean layered architecture |
| 2 | R-P12 | Add Redis/lifespan/auth integration fixtures; recover coverage ≥65% |
| 3 | R-P29 | Confirm `pytest-xdist` in `requirements-dev.txt`; `-n auto` in CI |
| 4 | R-P30 | Add `test_model_registry_thread_safety.py` (50 concurrent workers) |
| 5 | R-P31 | Add Redis denylist concurrency + expiry edge-case tests |
| 6 | R-P32 | Add `test_incident_service_contract.py` |
| 7 | R-P24 | Collapse deps to single pyproject.toml source of truth |
| 8 | R-P25–R-P27 | Add minimum credible content to `infrastructure/`, `dbt/`, `orchestration/` |

### Cycle 4 — Observability + Runbooks
*Wire live Prometheus metrics; elevate runbooks to operational grade.*

| Priority | ID | Action |
|----------|----|--------|
| 1 | R-P39 | Add `api/metrics.py` with Counter, Histogram, Gauge |
| 2 | R-P40 | Register metrics router in `api/main.py` |
| 3 | R-P41 | Instrument `create_incident` path with metric labels |
| 4 | R-P38 | Add `configs/slos.yml` with numeric SLO definitions |
| 5 | R-P42–R-P43 | Bind SLOs to Prometheus expressions; update Grafana dashboard |
| 6 | R-P34–R-P37 | Expand all runbooks to operational template; add model_rollback, feature_store_corruption, runbook_test_log |

### Cycle 5 — HF Deployment
*Get live public endpoint running on HF Spaces.*

| Priority | ID | Action |
|----------|----|--------|
| 1 | R-P44–R-P45 | Confirm HF Space slug; verify Dockerfile port |
| 2 | R-P46–R-P47 | Provision Neon Postgres + Upstash Redis; set HF Secrets |
| 3 | R-P48 | Add `scripts/seed_demo_user.py` |
| 4 | R-P49 | Add `make deploy-hf` + `make hf-status` targets |
| 5 | R-P50 | Smoke-test live endpoint |

### Cycle 6 — Portfolio Polish
*Final README pass; ensure 90-second reviewer experience is compelling.*

| Priority | ID | Action |
|----------|----|--------|
| 1 | R-P33 | Fix README coverage discrepancy (`≥68%` vs actual `75%` gate) |
| 2 | R-P28 | Update Architecture Mermaid diagram to match real code path |
| 3 | R-P51 | Add "What Senior Reviewers Will Find" section |
| 4 | R-P52 | Add "Known Limitations" callout |
| 5 | R-P53 | Update Roadmap to Q3/Q4/Aspirational strategic format |
| 6 | R-P54–R-P55 | Verify all badge URLs + internal doc links |
| 7 | R-P56 | Final README pass — remove stale Fly.io refs; verify `zrl.dev` link |
